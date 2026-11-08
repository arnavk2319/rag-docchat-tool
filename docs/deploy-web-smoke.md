# Live staging smoke (T-047)

**HUMAN-RUN. Not executed by an agent (no staging backend is deployed). Results below are blank.**
Run after every staging deploy of the backend or the web app. Prereqs: `curl`, `python3`; backend with
`CORS_ALLOW_ORIGINS` set to the staging web origin.

```bash
export API=https://api-staging.example.com        # backend origin = VITE_API_BASE_URL
export WEB=https://docchat-staging.example.app    # web origin (stable alias)
```

## 1. Health and CORS preflight (with the real Authorization header)
```bash
curl -s $API/health                                              # {"status":"ok"}
curl -si -X OPTIONS $API/v1/query \
  -H "Origin: $WEB" -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: authorization,content-type" | grep -i -e '^HTTP' -e access-control
#  expect 200, access-control-allow-origin: $WEB, allow-headers includes Authorization and Content-Type
curl -si -X OPTIONS $API/v1/query -H "Origin: https://evil.example" \
  -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: authorization" \
  | grep -i access-control-allow-origin                           # expect: no output
```

## 2. Mint (T-034)
```bash
curl -s -X POST $API/v1/auth/anonymous -H "Origin: $WEB" | tee /tmp/mint.json
#  expect {"access_token":"...","token_type":"bearer","expires_at":<unix seconds>}
export T=$(python3 -c "import json;print(json.load(open('/tmp/mint.json'))['access_token'])")
export H="Authorization: Bearer $T"
curl -s $API/v1/documents                                          # 401 {"code":"unauthorized"}
curl -s -H "Authorization: Bearer abc.def" $API/v1/documents       # 401 {"code":"invalid_token"}
```
Mint 429 (`rate_limited`, with `Retry-After`) appears after `ANON_MINT_RATE_PER_HOUR` mints per IP; the web app shows "Too many visitors".

## 3. Upload
```bash
printf 'Revenue was 4.2 million in fiscal 2025.\n' > /tmp/r.txt
curl -s -H "$H" -H "Origin: $WEB" -F files=@/tmp/r.txt $API/v1/documents | tee /tmp/up.json
#  expect results[0].ok=true, status "ready"; bad.exe -> error.code bad_extension
export DOC=$(python3 -c "import json;print(json.load(open('/tmp/up.json'))['results'][0]['doc_id'])")
curl -s -H "$H" "$API/v1/documents?limit=50&offset=0"
```

## 4. Sessions
```bash
curl -si -X POST -H "$H" $API/v1/sessions | tee /tmp/s.txt      # 201 {"session_id":"..."}
export SID=$(tail -1 /tmp/s.txt | python3 -c "import json,sys;print(json.load(sys.stdin)['session_id'])")
curl -s -H "$H" "$API/v1/sessions?limit=20"
curl -s -H "$H" $API/v1/sessions/$SID
```

## 5. Chat stream (SSE) and non-stream
```bash
curl -sN -H "$H" -H 'Content-Type: application/json' -H "Origin: $WEB" \
  -d "{\"question\":\"What was revenue?\",\"stream\":true,\"session_id\":\"$SID\",\"doc_ids\":[\"$DOC\"]}" \
  $API/v1/query
#  expect events step/tool_call/tool_result/token/citations then exactly one final; every data has run_id and seq
curl -s -H "$H" -H 'Content-Type: application/json' -d '{"question":"What was revenue?"}' $API/v1/query
```

## 6. Chunk passage
```bash
CH=$(curl -s -H "$H" -H 'Content-Type: application/json' -d "{\"question\":\"revenue\",\"doc_ids\":[\"$DOC\"]}" \
  $API/v1/query | python3 -c "import json,sys;print(json.load(sys.stdin)['citations'][0]['chunk_id'])")
curl -s -H "$H" "$API/v1/documents/$DOC/chunks/$CH"   # {doc_id,chunk_id,filename,text,highlight{start,end}}
curl -s -H "$H" $API/v1/documents/$DOC/chunks/nope    # 404 {"code":"not_found"}
```

## 7. Delete
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE -H "$H" $API/v1/documents/$DOC   # 204
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE -H "$H" $API/v1/documents/$DOC   # 404
```

## 8. Browser pass
Open `$WEB` with DevTools: Network shows requests only to `$WEB` and `$API`; no `mockServiceWorker.js`; no CSP violations.

## Results (HUMAN fills in)
| Step | Date | Pass/Fail | Notes |
|---|---|---|---|
| 1 Health/CORS | | | |
| 2 Mint | | | |
| 3 Upload | | | |
| 4 Sessions | | | |
| 5 Stream | | | |
| 6 Chunk | | | |
| 7 Delete | | | |
| 8 Browser | | | |

## Escaping matrix (backend source audit, `core/security.py`)
`sanitize_output`/`sanitize_untrusted` HTML-escape `& < >` once (and defang links). The frontend decodes exactly the escaped fields via `decodeEntities` (`web/src/api/text.ts`) and never decodes raw ones.

| Field | Escaped by backend? | Frontend |
|---|---|---|
| SSE `token.text`, `final.answer`, step `summary`, tool names/args | yes (`api/sse.py`) | decoded in `chat/reducer.ts` |
| SSE citation `filename`, `sheet`, `chunk_id`, `doc_id` | yes (`sse.py` build_citations) | `filename`, `sheet` decoded; ids used as opaque keys/URL parts (raw escaped id is what the chunk route expects only if it has no `& < >`) |
| `POST /v1/query` (non-stream) `answer` | yes | not used by the UI; would need decoding |
| non-stream `citations[].filename/sheet` | NO (raw, `rag.citation_for`) | not used by the UI; do not decode |
| `GET /v1/sessions/{id}` `messages[].content` (both roles) | yes (`sanitize_output` at read) | decoded on `load` (fixed in T-047) |
| `GET /v1/sessions` `title` | yes (`make_title`) | decoded in `SessionList` (fixed in T-047) |
| `DocumentOut.filename`, `failure_reason`, `UploadResult.filename` | NO (raw, basename sanitised only) | rendered as text, not decoded; `failure_reason` is not rendered |
| `UploadResult.error.detail`, `ErrorBody.detail` | NO (fixed strings) | never rendered; `messageFor(code)` is used |
| `ChunkPassage.text`, `.filename` | NO (raw JSON data, T-048) | rendered as text, not decoded |
| `/summary`, `/sentiment`, `/compare` text | yes | owned by T-046 (docintel) |
