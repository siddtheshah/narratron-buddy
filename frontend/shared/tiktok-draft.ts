const TIKTOK_STATUS_INTERVAL_MS = 5_000;
const TIKTOK_STATUS_MAX_CHECKS = 12;

interface TikTokErrorDetail {
  readonly message?: string;
  readonly code?: string;
}

interface TikTokErrorResponse {
  readonly detail?: string | TikTokErrorDetail;
}

interface TikTokUploadResponse {
  readonly publish_id: string;
  readonly upload_url?: string;
}

interface TikTokDraftStatusResponse {
  readonly publish_id: string;
  readonly status?: string;
  readonly fail_reason?: string;
}

interface TikTokDraftHandoffOptions {
  readonly uploadMp4OnServer?: boolean;
  readonly onStatus?: (message: string) => void;
}

export function downloadClip(blob: Blob, mimeType: string): void {
  const extension = mimeType.startsWith('video/mp4') ? 'mp4' : 'webm';
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `narratron-tiktok-clip-${new Date().toISOString().replace(/[:.]/g, '-')}.${extension}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Connect TikTok and send the locally reviewed clip to the user's Inbox. */
export function initializeTikTokDraftHandoff(
  root: HTMLElement | null,
  signal: AbortSignal,
  { uploadMp4OnServer = false, onStatus }: TikTokDraftHandoffOptions = {},
): (clip: Blob) => void {
  const clipTikTokDraftLink = root?.querySelector<HTMLButtonElement>('#clip-tiktok-draft-link');
  const clipTikTokOpenLink = root?.querySelector<HTMLElement>('#clip-tiktok-open-link');
  const clipDraftStatus = root?.querySelector<HTMLElement>('#clip-draft-status');
  const clipDraftIntro = root?.querySelector<HTMLElement>('#clip-draft-intro');
  const clipTikTokInboxHelp = root?.querySelector<HTMLElement>('#clip-tiktok-inbox-help');
  let completedClip: Blob | null = null;

  function setDraftStatus(message: string): void {
    if (clipDraftStatus) clipDraftStatus.textContent = message;
    if (message) onStatus?.(message);
  }

  async function checkTikTokDraftDelivery(publishId: string): Promise<void> {
    for (let check = 0; check < TIKTOK_STATUS_MAX_CHECKS; check += 1) {
      await new Promise<void>((resolve) => setTimeout(resolve, TIKTOK_STATUS_INTERVAL_MS));
      if (signal.aborted) return;

      try {
        const response = await fetch('/api/tiktok/draft-status', {
          signal: AbortSignal.any([signal, AbortSignal.timeout(TIKTOK_STATUS_INTERVAL_MS)]),
        });
        if (!response.ok) continue;
        const status = (await response.json()) as TikTokDraftStatusResponse;
        if (signal.aborted) return;
        if (status.publish_id !== publishId) break;
        switch (status.status) {
          case 'SEND_TO_USER_INBOX':
            setDraftStatus('Delivered to your TikTok Inbox.');
            return;
          case 'PUBLISH_COMPLETE':
            if (clipTikTokInboxHelp) clipTikTokInboxHelp.hidden = true;
            setDraftStatus('Your clip was published on TikTok.');
            return;
          case 'FAILED':
            if (clipTikTokInboxHelp) clipTikTokInboxHelp.hidden = true;
            setDraftStatus(
              status.fail_reason
                ? `TikTok couldn’t prepare the draft (${status.fail_reason}).`
                : 'TikTok couldn’t prepare the draft.',
            );
            return;
        }
      } catch {
        // A failed check can be transient. The Inbox remains available while we retry.
      }
    }
    if (!signal.aborted) setDraftStatus('Clip sent. Delivery hasn’t been confirmed.');
  }

  function sendClipToTikTokDraft(): void {
    if (!completedClip || !clipTikTokDraftLink || clipTikTokDraftLink.disabled) return;
    const sendButton = clipTikTokDraftLink;
    const clipToUpload = completedClip;
    setDraftStatus('Preparing secure TikTok connection…');
    const popup = window.open(
      '/api/tiktok/connect',
      'narratron-tiktok-connect',
      'popup=yes,width=520,height=720',
    );
    if (!popup) {
      setDraftStatus('Allow pop-ups to connect TikTok.');
      return;
    }

    sendButton.disabled = true;
    function finishConnection(): void {
      window.clearInterval(connectionTimer);
      window.removeEventListener('message', connected);
      signal.removeEventListener('abort', finishConnection);
    }
    const connectBy = Date.now() + 10 * 60_000;
    const connectionTimer = window.setInterval(() => {
      if (!popup.closed && Date.now() < connectBy) return;
      finishConnection();
      sendButton.disabled = false;
      setDraftStatus('TikTok connection was not completed. Try again.');
    }, 1000);
    signal.addEventListener('abort', finishConnection, { once: true });

    async function connected(event: MessageEvent): Promise<void> {
      const payload = event.data as { type?: string } | null;
      if (event.origin !== window.location.origin) return;
      if (payload?.type === 'narratron-tiktok-error') {
        finishConnection();
        sendButton.disabled = false;
        setDraftStatus('TikTok connection failed. Try again.');
        return;
      }
      if (payload?.type !== 'narratron-tiktok-connected') return;
      finishConnection();
      try {
        setDraftStatus('Sending clip to TikTok…');
        const baseMimeType = (clipToUpload.type || 'video/webm').split(';', 1)[0];
        const isWebM = baseMimeType === 'video/webm';
        const uploadOnServer = isWebM || uploadMp4OnServer;
        const form = new FormData();
        if (uploadOnServer) form.append('clip', clipToUpload, isWebM ? 'narratron-clip.webm' : 'narratron-clip.mp4');
        const response =
          uploadOnServer
            ? await fetch(isWebM ? '/api/tiktok/draft-upload-transcoded' : '/api/tiktok/draft-upload-mp4', {
                method: 'POST',
                signal,
                body: form,
              })
            : await fetch('/api/tiktok/draft-upload', {
                method: 'POST',
                signal,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  video_size: clipToUpload.size,
                  mime_type: baseMimeType,
                }),
              });
        if (!response.ok) {
          const failure = (await response.json().catch(() => ({}))) as TikTokErrorResponse;
          const detail = failure.detail;
          const message =
            typeof detail === 'string'
              ? detail
              : detail?.message || detail?.code || JSON.stringify(detail || failure);
          throw new Error(message || 'TikTok draft setup failed.');
        }
        const { publish_id, upload_url } = (await response.json()) as TikTokUploadResponse;
        if (!uploadOnServer) {
          if (!upload_url) throw new Error('TikTok didn’t provide an upload URL.');
          const put = await fetch(upload_url, {
            method: 'PUT',
            signal,
            headers: {
              'Content-Type': baseMimeType,
              'Content-Length': String(clipToUpload.size),
              'Content-Range': `bytes 0-${clipToUpload.size - 1}/${clipToUpload.size}`,
            },
            body: clipToUpload,
          });
          if (!put.ok) {
            throw new Error('TikTok could not receive the clip.');
          }
        }
        completedClip = null;
        sendButton.hidden = true;
        if (clipDraftIntro) clipDraftIntro.hidden = true;
        if (clipTikTokOpenLink) clipTikTokOpenLink.hidden = false;
        if (clipTikTokInboxHelp) clipTikTokInboxHelp.hidden = false;
        setDraftStatus('Clip sent. TikTok is preparing your draft.');
        void checkTikTokDraftDelivery(publish_id);
      } catch (error) {
        if (signal.aborted) return;
        sendButton.disabled = false;
        setDraftStatus(error instanceof Error ? error.message : 'Unable to send the clip.');
      }
    }

    window.addEventListener('message', connected, { signal });
  }

  clipTikTokDraftLink?.addEventListener('click', sendClipToTikTokDraft, { signal });

  return (clip: Blob): void => {
    completedClip = clip;
    if (clipTikTokDraftLink) {
      clipTikTokDraftLink.hidden = false;
      clipTikTokDraftLink.disabled = false;
    }
    if (clipDraftIntro) clipDraftIntro.hidden = false;
    if (clipTikTokOpenLink) clipTikTokOpenLink.hidden = true;
    if (clipTikTokInboxHelp) clipTikTokInboxHelp.hidden = true;
    setDraftStatus('');
  };
}
