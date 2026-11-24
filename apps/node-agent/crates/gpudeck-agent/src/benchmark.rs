//! `run_benchmark`: load test a local instance at 127.0.0.1:port (bypassing the proxy), one
//! level per concurrency, streaming requests timed for TTFT / e2e / TPOT, `benchmark_progress`
//! events per level, SLO check, best concurrency and a recommendation. With `auto_tune` the
//! instance is restarted with each flag variant (only when it has no live traffic) and the
//! original launch flags are restored at the end. Result rows follow `benchmark_runs.results`.

use std::sync::Arc;
use std::time::{Duration, Instant};

use futures::StreamExt;
use gpudeck_protocol::{
    error_code, ErrorInfo, Event, EventKind, JsonObject, ModelType, RunBenchmarkArgs, TargetSlo,
    UnloadModelArgs,
};
use serde_json::{json, Value};

use crate::agent::Agent;
use crate::commands::Ctx;
use crate::proxy::body::has_content;
use crate::proxy::stats::{mean, percentile, sort};

#[derive(Debug, Clone, Default)]
struct Sample {
    ok: bool,
    ttft_ms: Option<f64>,
    e2e_ms: f64,
    out_tokens: u64,
    in_tokens: u64,
}

fn synthetic_prompt(tokens: u64, salt: usize) -> String {
    let words = [
        "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet",
    ];
    let mut s = format!("Request {salt}. Summarise the following list in one paragraph: ");
    for i in 0..tokens.saturating_sub(12) {
        s.push_str(words[(i as usize + salt) % words.len()]);
        s.push(' ');
    }
    s
}

fn request_body(a: &RunBenchmarkArgs, prompt: &str) -> Value {
    let w = &a.workload;
    match a.model_type {
        ModelType::Embedding => json!({"model": a.served_model_name, "input": prompt}),
        ModelType::Rerank => {
            json!({"model": a.served_model_name, "query": prompt, "documents": [prompt, "unrelated text"]})
        }
        _ if a.endpoint.ends_with("/completions") && !a.endpoint.ends_with("/chat/completions") => {
            json!({"model": a.served_model_name, "prompt": prompt, "max_tokens": w.output_tokens, "stream": w.stream, "stream_options": {"include_usage": true}})
        }
        _ => {
            let mut v = json!({"model": a.served_model_name, "messages": [{"role": "user", "content": prompt}], "max_tokens": w.output_tokens, "stream": w.stream});
            if w.stream {
                v["stream_options"] = json!({"include_usage": true});
            }
            v
        }
    }
}

async fn one(client: &reqwest::Client, url: &str, body: Value, stream: bool) -> Sample {
    let started = Instant::now();
    let mut s = Sample::default();
    let resp = match client.post(url).json(&body).send().await {
        Ok(r) => r,
        Err(_) => {
            s.e2e_ms = started.elapsed().as_secs_f64() * 1000.0;
            return s;
        }
    };
    let ok_status = resp.status().is_success();
    let mut rec = crate::proxy::stats::RequestRecord::default();
    if stream {
        let mut chunks = 0u64;
        let mut buf = Vec::new();
        let mut bs = resp.bytes_stream();
        while let Some(Ok(b)) = bs.next().await {
            buf.extend_from_slice(&b);
            while let Some(pos) = buf.iter().position(|c| *c == b'\n') {
                let line: Vec<u8> = buf.drain(..=pos).collect();
                let line = String::from_utf8_lossy(&line);
                let Some(p) = line.trim().strip_prefix("data:") else {
                    continue;
                };
                let Ok(v) = serde_json::from_str::<Value>(p.trim()) else {
                    continue;
                };
                if has_content(&v) {
                    chunks += 1;
                    if s.ttft_ms.is_none() {
                        s.ttft_ms = Some(started.elapsed().as_secs_f64() * 1000.0);
                    }
                }
                rec.absorb(&v);
            }
        }
        s.out_tokens = rec.completion_tokens.unwrap_or(chunks);
    } else {
        let v: Value = resp.json().await.unwrap_or(Value::Null);
        s.ttft_ms = Some(started.elapsed().as_secs_f64() * 1000.0);
        rec.absorb(&v);
        s.out_tokens = rec.completion_tokens.unwrap_or(0);
    }
    s.in_tokens = rec.prompt_tokens.unwrap_or(0);
    s.ok = ok_status;
    s.e2e_ms = started.elapsed().as_secs_f64() * 1000.0;
    s
}

fn pct(v: &mut [f64]) -> Value {
    sort(v);
    json!({"p50": percentile(v, 0.5), "p95": percentile(v, 0.95), "p99": percentile(v, 0.99), "avg": mean(v)})
}

fn slo_met(row: &Value, slo: &TargetSlo) -> bool {
    let get = |m: &str| {
        row.get(m)
            .and_then(|x| x.get("p95"))
            .and_then(|x| x.as_f64())
    };
    let le = |bound: Option<f64>, m: &str| bound.is_none_or(|b| get(m).is_some_and(|v| v <= b));
    let err = row
        .get("error_rate_pct")
        .and_then(|x| x.as_f64())
        .unwrap_or(100.0);
    let tps = row
        .get("per_request_generation_tps")
        .and_then(|x| x.get("p50"))
        .and_then(|x| x.as_f64());
    le(slo.ttft_ms_p95, "ttft_ms")
        && le(slo.e2e_ms_p95, "e2e_ms")
        && le(slo.tpot_ms_p95, "tpot_ms")
        && err <= slo.max_error_rate_pct
        && slo
            .min_generation_tps
            .is_none_or(|m| tps.is_some_and(|t| t >= m))
}

async fn run_level(
    a: &RunBenchmarkArgs,
    port: u16,
    concurrency: u32,
    cancel: &tokio_util::sync::CancellationToken,
) -> Vec<Sample> {
    let client = crate::tls::http_client_builder()
        .no_proxy()
        .timeout(Duration::from_secs(600))
        .build()
        .expect("client");
    let url = format!("http://127.0.0.1:{port}{}", a.endpoint);
    let deadline = Instant::now() + Duration::from_secs(a.duration_seconds.max(5));
    let total = a.workload.requests_per_level.max(1) as usize;
    let next = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let results = Arc::new(tokio::sync::Mutex::new(Vec::with_capacity(total)));
    let mut workers = vec![];
    for _ in 0..concurrency.max(1) {
        let (client, url, next, results, cancel) = (
            client.clone(),
            url.clone(),
            next.clone(),
            results.clone(),
            cancel.clone(),
        );
        let a = a.clone();
        workers.push(tokio::spawn(async move {
            loop {
                let i = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                if i >= total || Instant::now() >= deadline || cancel.is_cancelled() {
                    break;
                }
                let prompt = if a.workload.prompts.is_empty() {
                    synthetic_prompt(a.workload.input_tokens, i)
                } else {
                    a.workload.prompts[i % a.workload.prompts.len()].clone()
                };
                let s = one(
                    &client,
                    &url,
                    request_body(&a, &prompt),
                    a.workload.stream
                        && !matches!(a.model_type, ModelType::Embedding | ModelType::Rerank),
                )
                .await;
                results.lock().await.push(s);
            }
        }));
    }
    for w in workers {
        let _ = w.await;
    }
    let v = results.lock().await.clone();
    v
}

fn summarize(
    concurrency: u32,
    samples: &[Sample],
    wall_s: f64,
    slo: &TargetSlo,
    flags: &JsonObject,
) -> Value {
    let ok: Vec<&Sample> = samples.iter().filter(|s| s.ok).collect();
    let errors = samples.len() - ok.len();
    let mut ttft: Vec<f64> = ok.iter().filter_map(|s| s.ttft_ms).collect();
    let mut e2e: Vec<f64> = ok.iter().map(|s| s.e2e_ms).collect();
    let mut tpot: Vec<f64> = ok
        .iter()
        .filter(|s| s.out_tokens > 1)
        .map(|s| (s.e2e_ms - s.ttft_ms.unwrap_or(0.0)) / (s.out_tokens - 1) as f64)
        .collect();
    let mut per_req_tps: Vec<f64> = ok
        .iter()
        .filter(|s| s.out_tokens > 0 && s.e2e_ms > s.ttft_ms.unwrap_or(0.0))
        .map(|s| s.out_tokens as f64 * 1000.0 / (s.e2e_ms - s.ttft_ms.unwrap_or(0.0)).max(1.0))
        .collect();
    let out: u64 = ok.iter().map(|s| s.out_tokens).sum();
    let inp: u64 = ok.iter().map(|s| s.in_tokens).sum();
    let wall = wall_s.max(0.001);
    let mut row = json!({
        "concurrency": concurrency,
        "requests": samples.len(),
        "errors": errors,
        "error_rate_pct": if samples.is_empty() { 100.0 } else { errors as f64 * 100.0 / samples.len() as f64 },
        "rps": ok.len() as f64 / wall,
        "generation_tps": out as f64 / wall,
        "prompt_tps": inp as f64 / wall,
        "ttft_ms": pct(&mut ttft),
        "e2e_ms": pct(&mut e2e),
        "tpot_ms": pct(&mut tpot),
        "per_request_generation_tps": pct(&mut per_req_tps),
        "duration_seconds": wall_s,
        "launch_flags": flags,
    });
    let met = slo_met(&row, slo);
    row["slo_met"] = met.into();
    row
}

/// Pick the best SLO-meeting row (highest concurrency, then throughput).
pub fn recommend(rows: &[Value]) -> (Option<u32>, Value) {
    let met: Vec<&Value> = rows
        .iter()
        .filter(|r| r["slo_met"].as_bool() == Some(true))
        .collect();
    let key = |r: &&Value| {
        (
            r["concurrency"].as_u64().unwrap_or(0),
            (r["generation_tps"].as_f64().unwrap_or(0.0) * 1000.0) as u64,
        )
    };
    match met.iter().copied().max_by_key(key) {
        Some(best) => {
            let c = best["concurrency"].as_u64().unwrap_or(1) as u32;
            (
                Some(c),
                json!({"concurrency": c, "launch_flags": best["launch_flags"], "replicas": 1, "reason": format!("highest concurrency meeting the SLO ({:.1} tok/s)", best["generation_tps"].as_f64().unwrap_or(0.0))}),
            )
        }
        None => {
            let low = rows
                .iter()
                .min_by_key(|r| r["concurrency"].as_u64().unwrap_or(u64::MAX));
            let reason = match low.and_then(|r| r["ttft_ms"]["p95"].as_f64()) {
                Some(t) => format!("SLO not met at any concurrency (lowest level TTFT p95 {t:.0} ms); try the low_latency preset, a smaller context or more replicas"),
                None => "SLO not met at any concurrency".into(),
            };
            (
                None,
                json!({"concurrency": null, "launch_flags": {}, "replicas": 2, "reason": reason}),
            )
        }
    }
}

async fn sweep(
    agent: &Arc<Agent>,
    ctx: &Ctx,
    a: &RunBenchmarkArgs,
    port: u16,
    flags: &JsonObject,
    step0: usize,
    steps: usize,
) -> Result<Vec<Value>, ErrorInfo> {
    let mut rows = vec![];
    for (i, c) in a.concurrency_levels.iter().enumerate() {
        if ctx.cancel.is_cancelled() {
            return Err(ErrorInfo::new(error_code::CANCELLED, "cancelled"));
        }
        let started = Instant::now();
        let samples = run_level(a, port, *c, &ctx.cancel).await;
        let row = summarize(
            *c,
            &samples,
            started.elapsed().as_secs_f64(),
            &a.target_slo,
            flags,
        );
        let done = step0 + i + 1;
        let pct = done as f64 * 100.0 / steps as f64;
        ctx.progress(
            Some(pct.min(99.0)),
            format!(
                "concurrency {c}: {:.1} tok/s",
                row["generation_tps"].as_f64().unwrap_or(0.0)
            ),
        );
        let mut d = row.as_object().cloned().unwrap_or_default();
        d.insert(
            "benchmark_run_id".into(),
            a.benchmark_run_id.to_string().into(),
        );
        d.insert("progress_pct".into(), pct.into());
        agent.event(
            Event::info(
                EventKind::BenchmarkProgress,
                format!("benchmark {} level {c} done", a.benchmark_run_id),
            )
            .instance(a.instance_id)
            .details(d),
        );
        rows.push(row);
    }
    Ok(rows)
}

pub async fn run(agent: &Arc<Agent>, ctx: &Ctx, a: RunBenchmarkArgs) -> Result<Value, ErrorInfo> {
    if a.concurrency_levels.is_empty() {
        return Err(ErrorInfo::new(
            error_code::INVALID_ARGS,
            "concurrency_levels is empty",
        ));
    }
    let base_args = agent
        .instances
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .get(&a.instance_id)
        .map(|i| (i.args.clone(), i.port));
    let port = base_args
        .as_ref()
        .map(|(_, p)| *p)
        .filter(|p| *p != 0)
        .unwrap_or(a.port);
    let variants: Vec<JsonObject> = a
        .auto_tune
        .as_ref()
        .map(|t| t.flag_variants.clone())
        .unwrap_or_default();
    let steps = a.concurrency_levels.len() * (1 + variants.len());
    let base_flags = base_args
        .as_ref()
        .map(|(x, _)| x.launch.flags.clone())
        .unwrap_or_default();
    let mut rows = sweep(agent, ctx, &a, port, &base_flags, 0, steps).await?;
    if !variants.is_empty() {
        let Some((orig, _)) = base_args.clone() else {
            return Err(ErrorInfo::new(
                error_code::INSTANCE_NOT_FOUND,
                "auto_tune needs an instance managed by this agent",
            ));
        };
        let (act, wait) = agent.proxy.counts(&a.instance_id);
        if act + wait > 0 {
            return Err(ErrorInfo::new(
                error_code::INVALID_ARGS,
                "auto_tune is only allowed on instances with no live traffic",
            ));
        }
        for (vi, v) in variants.iter().enumerate() {
            let mut args = orig.clone();
            for (k, val) in v {
                args.launch.flags.insert(k.clone(), val.clone());
            }
            ctx.progress(
                None,
                format!(
                    "auto-tune variant {}/{}: restarting instance",
                    vi + 1,
                    variants.len()
                ),
            );
            agent
                .unload_model(
                    &Ctx::internal(),
                    UnloadModelArgs {
                        instance_id: a.instance_id,
                        force: true,
                        drain_timeout_seconds: 0,
                        kill_grace_seconds: 15,
                        stop_engine_if_empty: false,
                    },
                )
                .await?;
            let loaded = agent.load_model(&Ctx::internal(), args.clone()).await;
            match loaded {
                Ok(res) => {
                    let p = res["port"].as_u64().unwrap_or(port as u64) as u16;
                    let mut vr = sweep(
                        agent,
                        ctx,
                        &a,
                        p,
                        &args.launch.flags,
                        (vi + 1) * a.concurrency_levels.len(),
                        steps,
                    )
                    .await?;
                    rows.append(&mut vr);
                }
                Err(e) => tracing::warn!("auto-tune variant {vi} failed to load: {e}"),
            }
        }
        // Restore the original launch flags.
        agent
            .unload_model(
                &Ctx::internal(),
                UnloadModelArgs {
                    instance_id: a.instance_id,
                    force: true,
                    drain_timeout_seconds: 0,
                    kill_grace_seconds: 15,
                    stop_engine_if_empty: false,
                },
            )
            .await?;
        agent.load_model(&Ctx::internal(), orig).await?;
    }
    let (best, rec) = recommend(&rows);
    Ok(
        json!({"benchmark_run_id": a.benchmark_run_id, "instance_id": a.instance_id, "results": rows, "best_concurrency": best, "slo_met": best.is_some(), "recommendation": rec}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn summary_and_recommendation() {
        let s = |ttft: f64, e2e: f64, n: u64| Sample {
            ok: true,
            ttft_ms: Some(ttft),
            e2e_ms: e2e,
            out_tokens: n,
            in_tokens: 10,
        };
        let slo = TargetSlo {
            ttft_ms_p95: Some(100.0),
            max_error_rate_pct: 1.0,
            ..Default::default()
        };
        let r1 = summarize(
            1,
            &[s(50.0, 1050.0, 101), s(60.0, 1060.0, 101)],
            2.0,
            &slo,
            &JsonObject::new(),
        );
        assert_eq!(r1["slo_met"], true);
        assert_eq!(r1["tpot_ms"]["p50"], 10.0);
        assert_eq!(r1["generation_tps"], 101.0);
        let r4 = summarize(4, &[s(500.0, 1500.0, 101)], 1.0, &slo, &JsonObject::new());
        assert_eq!(r4["slo_met"], false);
        let (best, rec) = recommend(&[r1.clone(), r4.clone()]);
        assert_eq!(best, Some(1));
        assert_eq!(rec["concurrency"], 1);
        let (none, rec) = recommend(&[r4]);
        assert!(none.is_none());
        assert!(rec["reason"].as_str().unwrap().contains("SLO not met"));
        let bad = Sample {
            ok: false,
            ..Default::default()
        };
        let r = summarize(2, &[bad], 1.0, &slo, &JsonObject::new());
        assert_eq!(r["error_rate_pct"], 100.0);
        assert!(synthetic_prompt(50, 1).split(' ').count() > 40);
    }
}
