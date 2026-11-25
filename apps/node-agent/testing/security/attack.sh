#!/usr/bin/env bash
# Runs inside the attacker / insider container (run.sh). Every check must FAIL CLOSED: nothing
# useful comes back. Prints one line per check: PASS|FAIL <check> -- <evidence>.
#
#   attack.sh scan    <tunnel-node-ip> <direct-node-ip>     # port scans (attacker)
#   attack.sh tls     <direct-node-ip>                      # TLS identities against :9443 (insider)
#   attack.sh precheck <direct-node-ip>                     # non-allow-listed source, no firewall
#   attack.sh replay  <used-join-token>                     # stolen join token (attacker)
#   attack.sh cptunnel                                      # control-plane tunnel listener
#   attack.sh ratelimit <direct-node-ip>                    # handshake flood (insider)
set -uo pipefail
C=/certs
CP=host.docker.internal
pass() { printf 'PASS %-58s -- %s\n' "$1" "$2"; }
fail() { printf 'FAIL %-58s -- %s\n' "$1" "$2"; }

# TLS client to host:port with an optional client cert; prints the HTTP status line if any HTTP
# byte came back, else "no-http(<openssl reason>)".
tls_try() {
  local target="$1" cert="${2:-}" key="${3:-}" args=() out
  [[ -n "$cert" ]] && args=(-cert "$cert" -key "$key")
  out="$(printf 'GET /_health HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n' \
    | timeout 8 openssl s_client -connect "$target" -tls1_3 -quiet -ign_eof "${args[@]}" 2>&1)"
  if grep -q '^HTTP/' <<<"$out"; then
    grep -m1 '^HTTP/' <<<"$out"
  else
    echo "no-http($(grep -m1 -oiE 'alert [a-z ]+|unexpected eof[a-z ]*|connection reset|Connection refused|errno=[0-9]+|certificate required|bad certificate|unknown ca' <<<"$out" | head -1))"
  fi
}

case "${1:-}" in
  scan)
    tnode="$2" dnode="$3"
    open_t="$(nmap -Pn -sS -p- --min-rate 4000 -T4 "$tnode" 2>/dev/null | awk '/^[0-9]+\/tcp/ && $2=="open" {print $1}' | tr '\n' ' ')"
    if [[ "$open_t" == "2222/tcp " || -z "$open_t" ]]; then
      pass "tunnel node: full TCP scan (65535 ports)" "open: ${open_t:-none} (2222 = operator sshd, keys only)"
    else
      fail "tunnel node: full TCP scan (65535 ports)" "open: $open_t"
    fi
    open_d="$(nmap -Pn -sS -p- --min-rate 4000 -T4 "$dnode" 2>/dev/null | awk '/^[0-9]+\/tcp/ && $2=="open" {print $1}' | tr '\n' ' ')"
    st="$(nmap -Pn -sS -p 9443 "$dnode" 2>/dev/null | awk '/^9443\/tcp/ {print $2}')"
    if [[ "$open_d" == "2222/tcp " && "$st" == "filtered" ]]; then
      pass "direct node: full TCP scan from a non-allow-listed host" "open: $open_d; 9443 $st (dropped by the host firewall)"
    else
      fail "direct node: full TCP scan from a non-allow-listed host" "open: $open_d; 9443 $st"
    fi
    for n in "$tnode" "$dnode"; do
      st="$(nmap -Pn -sS -p 9100,10240-10340,2200-2221,2223-2299 "$n" 2>/dev/null | awk '/^[0-9]+\/tcp/ && $2=="open"' | wc -l | tr -d ' ')"
      [[ "$st" == 0 ]] && pass "engine / metrics / SSH-instance ports on $n" "0 open (9100, 10240-10340, 2200-2299)" \
        || fail "engine / metrics / SSH-instance ports on $n" "$st open"
    done
    r="$(tls_try "$dnode:9443" $C/cp.crt $C/cp.key)"
    [[ "$r" == no-http* ]] && pass "direct node :9443 TLS from a non-allow-listed host" "$r" || fail "direct node :9443 TLS from a non-allow-listed host" "$r"
    ;;
  tls)
    dnode="$2"
    for spec in "no client certificate||" "self-signed certificate|$C/self.crt|$C/self.key" \
                "certificate from another CA (CN=api-layer)|$C/evil.crt|$C/evil.key" \
                "valid GPU Deck service cert, not the pinned key|$C/cp.crt|$C/cp.key" \
                "valid GPU Deck hub-type cert, not the pinned key|$C/fakehub.crt|$C/fakehub.key"; do
      IFS='|' read -r what cert key <<<"$spec"
      r="$(tls_try "$dnode:9443" "$cert" "$key")"
      [[ "$r" == no-http* ]] && pass "insider (allow-listed IP) -> :9443, $what" "$r" || fail "insider -> :9443, $what" "$r"
    done
    ;;
  precheck)
    dnode="$2"
    r="$(tls_try "$dnode:9443" $C/cp.crt $C/cp.key)"
    [[ "$r" == no-http* ]] && pass "firewall removed: non-allow-listed source -> :9443" "$r (closed by the agent before TLS)" \
      || fail "firewall removed: non-allow-listed source -> :9443" "$r"
    ;;
  replay)
    token="$2"
    csr="$(openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj /CN=pending -keyout /tmp/k 2>/dev/null)"
    body="$(python3 -c 'import json,sys; print(json.dumps({"join_token":sys.argv[1],"csr_pem":sys.argv[2],"hostname":"evil","name":"evil","os":"linux","arch":"x86_64","agent_version":"1.0.0","protocol_version":1,"proxy_port":9443}))' "$token" "$csr")"
    code="$(curl -sk -o /tmp/r -w '%{http_code}' -H 'content-type: application/json' -d "$body" "https://$CP:8443/agent/v1/join")"
    [[ "$code" == 401 ]] && pass "replay a used join token at /agent/v1/join" "HTTP $code $(grep -o 'JOIN_TOKEN_[A-Z]*' /tmp/r | head -1)" || fail "replay a used join token" "HTTP $code $(head -c 200 /tmp/r)"
    code="$(curl -sk -o /dev/null -w '%{http_code}' -H "X-GPUDeck-Join-Token: $token" "https://$CP:8443/agent/v1/install.sh")"
    [[ "$code" == 401 ]] && pass "replay a used join token at /agent/v1/install.sh" "HTTP $code" || fail "replay at install.sh" "HTTP $code"
    code="$(curl -sk -o /dev/null -w '%{http_code}' -H "X-GPUDeck-Join-Token: $token" "https://$CP:8443/agent/v1/downloads/latest/aarch64-unknown-linux-gnu")"
    [[ "$code" == 401 ]] && pass "replay a used join token at /agent/v1/downloads" "HTTP $code" || fail "replay at downloads" "HTTP $code"
    ;;
  cptunnel)
    for spec in "no client certificate||" "self-signed certificate|$C/self.crt|$C/self.key" \
                "certificate from another CA|$C/evil.crt|$C/evil.key" \
                "valid GPU Deck service cert (not a node)|$C/cp.crt|$C/cp.key"; do
      IFS='|' read -r what cert key <<<"$spec"
      # TLS (ALPN h2) + the HTTP/2 client preface; a real tunnel peer answers with SETTINGS.
      r="$(python3 - "$CP" "$cert" "$key" <<'PY'
import socket, ssl, sys
host, cert, key = sys.argv[1], sys.argv[2], sys.argv[3]
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
ctx.set_alpn_protocols(["h2"])
if cert:
    ctx.load_cert_chain(cert, key)
try:
    s = ctx.wrap_socket(socket.create_connection((host, 8444), 5))
    s.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n" + b"\x00\x00\x00\x04\x00\x00\x00\x00\x00")
    s.settimeout(5)
    data = s.recv(100)
    print(f"bytes={len(data)}")
except ssl.SSLError as e:
    print(f"tls-error={e.reason}")
except Exception as e:
    print(f"closed={type(e).__name__}")
PY
)"
      if [[ "$r" == bytes=0 || "$r" == tls-error* || "$r" == closed* ]]; then
        pass "control-plane tunnel listener :8444, $what" "$r (no HTTP/2 frame)"
      else
        fail "control-plane tunnel listener :8444, $what" "$r"
      fi
    done
    ;;
  ratelimit)
    dnode="$2" ok=0 reset=0
    for i in $(seq 1 60); do
      if timeout 3 openssl s_client -connect "$dnode:9443" -tls1_3 </dev/null >/tmp/o 2>&1 && grep -q 'Server certificate' /tmp/o; then ok=$((ok+1)); else reset=$((reset+1)); fi
    done
    [[ "$reset" -gt 0 ]] && pass "handshake flood (60 in a row) from one source" "$ok reached TLS, $reset reset before TLS (rate limit)" \
      || fail "handshake flood" "all $ok reached TLS"
    ;;
  *) echo "usage: $0 scan|tls|precheck|replay|cptunnel|ratelimit ..." >&2; exit 2 ;;
esac
