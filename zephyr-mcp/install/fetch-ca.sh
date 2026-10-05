#!/usr/bin/env bash
# Собирает цепочку CA для сервера Jira/Zephyr в один PEM-файл для ZEPHYR_CA_BUNDLE.
# Сервер часто отдаёт только leaf-сертификат, поэтому недостающие CA докачиваются
# по ссылке «CA Issuers» (AIA) из самого сертификата. Закрытых ключей не касается.
#
# Использование: ./fetch-ca.sh https://tasks.example.com [выходной.pem]
set -euo pipefail

url="${1:-}"
[ -n "$url" ] || { echo "usage: $0 https://host[:port] [out.pem]" >&2; exit 2; }
out="${2:-$HOME/.config/zephyr-mcp/ca.pem}"

hostport="${url#*://}"; hostport="${hostport%%/*}"
port=443
if [[ "$hostport" == \[* ]]; then
  host="${hostport%%]*}"; host="${host#[}"
  rest="${hostport#*]}"; [[ "$rest" == :* ]] && port="${rest#:}"
else
  host="${hostport%%:*}"
  [[ "$hostport" == *:* ]] && port="${hostport##*:}"
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Сертификаты, отданные сервером: certN.pem; cert0 — leaf.
{ openssl s_client -connect "$host:$port" -servername "$host" -showcerts </dev/null 2>/dev/null || true; } \
  | awk -v d="$tmp" '/BEGIN CERTIFICATE/{n++; f=d"/srv"n".pem"; on=1} on{print > f} /END CERTIFICATE/{on=0; close(f)}'
[ -f "$tmp/srv1.pem" ] || { echo "не удалось получить сертификат от $host:$port" >&2; exit 1; }

subject() { openssl x509 -in "$1" -noout -subject | sed 's/^subject= *//'; }
issuer()  { openssl x509 -in "$1" -noout -issuer  | sed 's/^issuer= *//'; }
# Только http(s): ldap:// curl не скачает.
aia()     { openssl x509 -in "$1" -noout -text | sed -nE 's/.*CA Issuers - URI:(https?:\/\/.*)$/\1/p' | head -1; }

: > "$tmp/chain.pem"
last="$tmp/srv1.pem"
# Всё, что сервер отдал после leaf, — это уже CA.
for f in "$tmp"/srv[2-9].pem; do
  [ -f "$f" ] || continue
  cat "$f" >> "$tmp/chain.pem"
  last="$f"
done

# Докачиваем недостающие CA, пока не дойдём до самоподписанного корня.
for i in 1 2 3 4 5 6; do
  [ "$(subject "$last")" = "$(issuer "$last")" ] && break
  uri="$(aia "$last")"
  [ -n "$uri" ] || { echo "ВНИМАНИЕ: у '$(subject "$last")' нет ссылки на издателя; корневой CA запросите у администраторов" >&2; break; }
  echo "скачиваю CA: $uri" >&2
  curl -fsS --max-time 20 -o "$tmp/dl.cer" "$uri"
  next="$tmp/dl$i.pem"
  openssl x509 -inform DER -in "$tmp/dl.cer" -out "$next" 2>/dev/null \
    || openssl x509 -inform PEM -in "$tmp/dl.cer" -out "$next"
  # Скачанный по AIA CA должен реально подписать предыдущий сертификат.
  if ! openssl verify -partial_chain -CAfile "$next" "$last" >/dev/null 2>&1; then
    echo "ОШИБКА: сертификат с $uri не подписывал '$(subject "$last")', отбрасываю" >&2
    exit 1
  fi
  cat "$next" >> "$tmp/chain.pem"
  last="$next"
done

[ -s "$tmp/chain.pem" ] || { echo "ни одного CA получить не удалось" >&2; exit 1; }

awk -v d="$tmp" '/BEGIN CERTIFICATE/{n++; f=d"/o"n".pem"; on=1} on{print > f} /END CERTIFICATE/{on=0; close(f)}' "$tmp/chain.pem"
echo "Сертификаты в цепочке (сверьте с тем, чему доверяете):" >&2
for f in "$tmp"/o*.pem; do echo "  - $(subject "$f")" >&2; done

# Пишем в $out только после успешной проверки, чтобы не затереть рабочий файл.
if ! curl -sS -o /dev/null --cacert "$tmp/chain.pem" "https://$hostport/"; then
  echo "Проверка TLS НЕ прошла (в цепочке может не хватать корневого CA); $out не изменён" >&2
  exit 1
fi
mkdir -p "$(dirname "$out")"
cp "$tmp/chain.pem" "$out"
chmod 644 "$out"
echo "Проверка TLS: OK. Сохранено: $out" >&2

# Прописываем путь в env-файл сервера (остальные строки не трогаем).
envfile="$HOME/.config/zephyr-mcp/.env"
if [ -f "$envfile" ]; then
  CA_OUT="$out" awk '
    /^[[:space:]]*ZEPHYR_CA_BUNDLE[[:space:]]*=/ { if (!done) print "ZEPHYR_CA_BUNDLE=" ENVIRON["CA_OUT"]; done=1; next }
    { print }
    END { if (!done) print "ZEPHYR_CA_BUNDLE=" ENVIRON["CA_OUT"] }
  ' "$envfile" > "$tmp/env.new"
  cat "$tmp/env.new" > "$envfile"
  echo "ZEPHYR_CA_BUNDLE записан в $envfile" >&2
else
  echo "Файл $envfile не найден, добавьте вручную: ZEPHYR_CA_BUNDLE=$out" >&2
fi
