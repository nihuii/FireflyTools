def valid_edge_message():
    return {
        "protocol_version": 1,
        "type": "media_candidate",
        "request_id": "4e8ad6ef-94d5-4f53-8d30-2ac148183e3d",
        "captured_at": "2026-08-30T12:00:00Z",
        "page": {
            "url": "https://example.test/watch/1",
            "title": "Example",
        },
        "candidate": {
            "url": "https://cdn.example.test/master.m3u8?token=opaque",
            "kind": "hls",
            "content_type": "application/vnd.apple.mpegurl",
            "method": "GET",
            "headers": {
                "Referer": "https://example.test/",
                "Origin": "https://example.test",
                "User-Agent": "Edge UA",
                "Accept-Language": "zh-CN",
            },
        },
        "sensitive_headers_included": False,
    }
