# Backend MP4 API

Holo Video Uploader supports either of these HTTPS responses.

The backend should preferably return a normal single-view MP4. The app defaults
to the verified four-sided pyramid layout with the subject's head facing each
outer edge. If the backend already returns a four-way composite, the operator
must select `单画面全屏` in the app to avoid composing it a second time.

## Option A: JSON queue response

```http
GET /api/device/next HTTP/1.1
Accept: application/json, video/mp4
Authorization: Bearer <optional-token>
```

When a video is ready:

```json
{
  "id": "video-20260829-001",
  "name": "sleeping-cat.mp4",
  "download_url": "https://backend.example.com/files/video-20260829-001.mp4",
  "ack_url": "https://backend.example.com/api/device/ack"
}
```

- `id` must be stable and unique. The app uses it to avoid replaying the same queue item.
- `name` is optional.
- `download_url` is required. `mp4_url` and `url` are accepted aliases.
- `ack_url` is optional.
- Return `204 No Content` when there is no new video.
- The MP4 download must be no larger than 256 MB.

After the video is converted, uploaded and playing, the app sends this optional acknowledgement:

```http
POST /api/device/ack HTTP/1.1
Content-Type: application/json
Authorization: Bearer <optional-token>

{"id":"video-20260829-001","status":"played"}
```

The app records an item as processed only after this acknowledgement succeeds.
If the acknowledgement fails, automatic receive retries the item instead of
silently leaving the backend in a delivery state.

## Option B: return MP4 directly

```http
HTTP/1.1 200 OK
Content-Type: video/mp4
ETag: "video-20260829-001"
Content-Disposition: attachment; filename="sleeping-cat.mp4"

<MP4 bytes>
```

`ETag` is recommended. Without it, the app hashes the MP4 to detect duplicates.

## Authentication and transport

- Production endpoints must use HTTPS.
- An optional token entered in the app is sent as `Authorization: Bearer ...`.
- The token is kept only for the current app session and is not saved to disk.
- Managed launches may provide the same session-only token through
  `HOLO_DEVICE_TOKEN`; `HOLO_AUTO_RECEIVE=1` enables polling immediately.
- For JSON responses, the token is forwarded to `download_url` only when it uses the same host as the API endpoint.
- The same host restriction applies to `ack_url`.
- `http://localhost` and `http://127.0.0.1` are accepted only for local development.
