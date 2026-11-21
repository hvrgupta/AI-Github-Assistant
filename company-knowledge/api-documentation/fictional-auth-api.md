# Fictional Auth API

This document describes the fictional `fictional-auth-platform` API.

## Login

`POST /api/v1/login`

Request:

```json
{
  "username": "demo.user",
  "password": "redacted"
}
```

Successful response:

```json
{
  "access_token": "<redacted>",
  "refresh_token": "<redacted>",
  "expires_in": 900
}
```

Authentication failures use `401 Unauthorized`. Invalid input use
`400 Bad Request`. The API must not include passwords or token values in errors
or application logs.

## Token refresh

`POST /api/v1/token/refresh`

The refresh operation requires a current refresh token and returns a new token
pair. Refresh tokens must be rotated and may be used once.
