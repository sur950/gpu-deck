# Getting help

GPU Deck is maintained by one person in their spare time. This page says where to ask so questions reach the right place.

## Where to go

| You want to | Go to |
| --- | --- |
| Read how to install, run and operate GPU Deck | The [documentation site](https://sur950.github.io/gpu-deck/), [README.md](../README.md) and the [docs index](README.md) |
| Ask a question, share a setup, or get usage advice | [GitHub Discussions](https://github.com/sur950/gpu-deck/discussions) |
| Report a bug | A [bug report](https://github.com/sur950/gpu-deck/issues/new?template=bug_report.yml) |
| Suggest a feature | A [feature request](https://github.com/sur950/gpu-deck/issues/new?template=feature_request.yml), or Discussions first for early ideas |
| Tell us it works (or not) on your GPU or engine | A [hardware report](https://github.com/sur950/gpu-deck/issues/new?template=hardware_report.yml) |
| Report a security problem | A [private security advisory](https://github.com/sur950/gpu-deck/security/advisories/new), never a public issue. See [SECURITY.md](SECURITY.md) |
| Contribute code or docs | [CONTRIBUTING.md](../CONTRIBUTING.md) |

## Issues or discussions

Use an **issue** when something is broken or there is a concrete change to make. Use a **discussion** when you have a question, want to compare approaches, or are not sure it is a bug. A discussion can always be turned into an issue.

## Before you ask

1. Check the docs for your task: [DEPLOYMENT.md](DEPLOYMENT.md) explains what runs where, [operations.md](operations.md) has a troubleshooting section, and [security-model.md](security-model.md) lists the network rules, which explain most connection problems.
2. Search existing issues and discussions.
3. When you open an issue, give the version, the deployment type, the GPU vendor and the engine, and redact secrets from logs (API keys `gd-...`, join tokens `gdj_...`, vault keys, certificates).

GPU Deck has been verified end to end on Apple silicon. Other GPU vendors, the other engines (vLLM, SGLang, TensorRT-LLM, whisper.cpp, stable-diffusion.cpp and more), multi-node setups, Kubernetes and the cloud providers are implemented and tested against fixtures, so hardware reports from your setup are especially welcome. [TESTING.md](TESTING.md) lists what has run where.
