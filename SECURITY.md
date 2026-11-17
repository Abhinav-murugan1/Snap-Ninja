# Security

Snap Ninja is a trusted-user desktop application. Bind it to loopback only.
Do not expose it through a reverse proxy, public tunnel, or shared network.
Its API can read local media, create recordings, delete app data and upload to a
connected YouTube account. Request-origin and host checks protect against browser
cross-site requests and DNS rebinding; they do not authenticate local processes.

Keep Python dependencies, FFmpeg, yt-dlp, Ollama and the operating system updated.
Only process media and links you trust. Media decoding and model execution use
third-party software; application checks do not sandbox these components.

Ollama uses the configured `OLLAMA_HOST`. A remote host receives the supplied
content. Claude is optional and sends transcript content to an external paid API.
Video sources and Google services are contacted when those features are used.

Do not publish credentials or personal media in an issue. Until the repository
owner provides a private reporting address or enables GitHub private vulnerability
reporting, keep exploit details private and contact the owner directly.
