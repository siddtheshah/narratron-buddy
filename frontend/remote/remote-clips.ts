import { downloadClip, initializeTikTokDraftHandoff } from '../shared/tiktok-draft.js';

interface RemoteClipController {
  setCanRecord(allowed: boolean): void;
  disconnect(): void;
  destroy(): void;
}

type RecordingMessage = { type: 'recording' | 'processing' } | { type: 'error'; message: string };

export function initializeRemoteClips(
  theaterId: string | null,
  showDraftStatus: (message: string) => void,
): RemoteClipController | null {
  const toggle = document.querySelector<HTMLButtonElement>('#remote-clip-toggle');
  const progress = document.querySelector<HTMLProgressElement>('#remote-clip-progress');
  const status = document.getElementById('remote-clip-status');
  const review = document.getElementById('remote-clip-review');
  const preview = document.querySelector<HTMLDialogElement>('#remote-clip-preview');
  const previewButton = document.getElementById('remote-clip-preview-btn');
  const closePreview = document.getElementById('remote-clip-preview-close');
  const clear = document.getElementById('remote-clip-clear');
  const video = document.querySelector<HTMLVideoElement>('#remote-clip-video');
  const download = document.getElementById('remote-clip-download');
  if (!theaterId || !toggle || !progress || !status || !review || !preview || !previewButton || !closePreview || !clear || !video || !download) return null;

  const lifecycle = new AbortController();
  let draftLifecycle: AbortController | null = null;
  let socket: WebSocket | null = null;
  let clipUrl: string | null = null;
  let canRecord = false;
  let phase: 'idle' | 'starting' | 'recording' | 'processing' = 'idle';
  let progressFrame = 0;

  const render = (message?: string): void => {
    const label = phase === 'recording' || phase === 'processing' ? 'Stop' : 'Start';
    if (toggle.textContent !== label) toggle.textContent = label;
    toggle.dataset.recording = String(phase === 'recording');
    toggle.disabled = phase !== 'recording' && (phase !== 'idle' || !canRecord);
    progress.hidden = phase !== 'recording';
    if (progress.hidden) cancelAnimationFrame(progressFrame);
    if (message !== undefined && status.textContent !== message) status.textContent = message;
    const hasClip = phase === 'idle' && clipUrl !== null;
    previewButton.hidden = !hasClip;
    clear.hidden = !hasClip;
    review.hidden = !hasClip;
  };

  const startProgress = (): void => {
    const startedAt = performance.now();
    progress.value = 0;
    const advance = (now: number): void => {
      progress.value = Math.min(progress.max, now - startedAt);
      if (progress.value < progress.max) progressFrame = requestAnimationFrame(advance);
    };
    cancelAnimationFrame(progressFrame);
    progressFrame = requestAnimationFrame(advance);
  };

  function finish(message: string): void {
    const connection = socket;
    socket = null;
    connection?.close();
    phase = 'idle';
    render(message);
  }

  const releaseClip = (): void => {
    draftLifecycle?.abort();
    draftLifecycle = null;
    preview.close();
    if (video.hasAttribute('src')) {
      video.removeAttribute('src');
      video.load();
    }
    if (clipUrl) URL.revokeObjectURL(clipUrl);
    clipUrl = null;
  };

  const showClip = (blob: Blob): void => {
    releaseClip();
    draftLifecycle = new AbortController();
    const clip = blob.slice(0, blob.size, 'video/mp4');
    clipUrl = URL.createObjectURL(clip);
    download.addEventListener('click', () => downloadClip(clip, clip.type), { signal: draftLifecycle.signal });
    initializeTikTokDraftHandoff(review, draftLifecycle.signal, {
      uploadMp4OnServer: true,
      onStatus: showDraftStatus,
    })(clip);
    finish('');
  };

  const start = (): void => {
    const url = new URL('/ws/clip-recording', location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('theater_id', theaterId);
    const connection = new WebSocket(url);
    connection.binaryType = 'blob';
    socket = connection;
    phase = 'starting';
    render('Preparing the clip stage…');

    connection.addEventListener('message', (event: MessageEvent<string | Blob>) => {
      if (socket !== connection) return;
      if (typeof event.data !== 'string') {
        showClip(event.data);
        return;
      }
      const message = JSON.parse(event.data) as RecordingMessage;
      if (message.type === 'error') {
        finish(message.message);
      } else {
        phase = message.type;
        if (phase === 'recording') startProgress();
        render(phase === 'recording' ? '' : 'Preparing your clip…');
      }
    });
    connection.addEventListener('close', () => {
      if (socket === connection) finish('Recording connection lost. Try again.');
    });
  };

  toggle.addEventListener('click', () => {
    if (phase === 'recording' && socket?.readyState === WebSocket.OPEN) {
      socket.send('stop');
      phase = 'processing';
      render('Preparing your clip…');
    } else if (phase === 'idle' && canRecord) {
      start();
    }
  }, { signal: lifecycle.signal });

  const { signal } = lifecycle;
  previewButton.addEventListener('click', () => {
    if (!clipUrl) return;
    video.src = clipUrl;
    preview.showModal();
    void video.play().catch(() => {});
  }, { signal });
  closePreview.addEventListener('click', () => preview.close(), { signal });
  preview.addEventListener('close', () => {
    if (!preview.open) video.pause();
  }, { signal });
  clear.addEventListener('click', () => {
    releaseClip();
    render('');
    toggle.focus();
  }, { signal });

  return {
    setCanRecord(allowed: boolean): void {
      canRecord = allowed;
      render();
    },
    disconnect(): void {
      preview.close();
      if (socket) finish('Recording ended when you left the controller.');
    },
    destroy(): void {
      releaseClip();
      finish('');
      lifecycle.abort();
    },
  };
}
