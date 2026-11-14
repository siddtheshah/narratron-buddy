import { startCanvasStatePolling } from '../shared/state-polling.js';
import { createDoodleRenderer } from '../canvas/doodle-renderer.js';
import type { DoodleAnnotation } from '../canvas/doodle-types.js';
import { createImageRenderer, type ImageRenderer } from '../canvas/image-renderer.js';
import {
  getAnimationSignature,
  playCanvasAnimation,
  type CanvasAnimation,
} from '../canvas/media.js';
import { createNarrationPane } from '../canvas/narration-pane.js';
import {
  createCanvasScene,
  type SceneDialogueLine,
  type SceneTextSpan,
} from '../canvas/scene.js';

export interface ObsMusicState {
  readonly playlist?: string | null;
  readonly tracks?: readonly string[];
  readonly time?: number;
  readonly paused?: boolean;
}

export interface ObsHistoryEntry {
  readonly page_id?: string | null;
}

export interface ObsLatestResponse {
  readonly latest?: string;
  readonly time?: number;
  readonly effect?: string;
  readonly transition?: string;
  readonly animation?: CanvasAnimation | null;
  readonly narration?: string | null;
  readonly narration_spans?: readonly SceneTextSpan[] | null;
  readonly scene_dialogue?: readonly SceneDialogueLine[] | null;
  readonly committed_scene_speech_generation?: number | null;
  readonly music?: ObsMusicState | null;
  readonly doodle_page_id?: string | null;
  readonly orator_cursor?: number;
  readonly history?: readonly ObsHistoryEntry[];
  readonly doodles_enabled?: boolean;
}

export interface ObsDoodleAnnotation extends DoodleAnnotation {
  readonly stamp_id?: string | number;
  readonly user_id?: string | number;
}

export interface DoodleBatch {
  readonly points?: readonly number[];
  readonly color?: string;
  readonly size?: number;
}

export interface DoodleSnapshotPayload {
  readonly type?: string;
  readonly page_id?: string | null;
  readonly enabled?: boolean;
  readonly stamps?: readonly ObsDoodleAnnotation[];
  readonly batches?: readonly DoodleBatch[];
  readonly annotations?: readonly ObsDoodleAnnotation[];
}

interface DoodleSocketMessage extends DoodleSnapshotPayload {
  readonly type: string;
  readonly x0?: number;
  readonly y0?: number;
  readonly x1?: number;
  readonly y1?: number;
  readonly points?: readonly number[];
  readonly color?: string;
  readonly size?: number;
  readonly id?: string;
  readonly stamp_id?: string | number;
  readonly user_id?: string | number;
}

interface CanvasStateSocketMessage {
  readonly type: string;
  readonly domains?: readonly string[];
  readonly audio_url?: string;
  readonly speech_generation?: number;
}

interface FoundryMessage {
  readonly source?: string;
  readonly action?: string;
}

interface ObsDisplayMediaOptions extends DisplayMediaStreamOptions {
  selfBrowserSurface?: 'include' | 'exclude';
  preferCurrentTab?: boolean;
}

interface AudioMix {
  readonly tracks: MediaStreamTrack[];
  cleanup(): void;
}

interface ClipControl {
  stop(discard?: boolean): void;
}

interface TikTokErrorDetail {
  readonly message?: string;
  readonly code?: string;
}

interface TikTokErrorResponse {
  readonly detail?: string | TikTokErrorDetail;
}

interface TikTokUploadResponse {
  readonly upload_url: string;
}

interface TikTokDraftStatusResponse {
  readonly status?: string;
  readonly fail_reason?: string;
}

export interface ObsCanvasController {
  fetchLatestImage(): Promise<void>;
  syncMusic(music: ObsMusicState | null | undefined): void;
  dispose(): void;
}

const CLIP_DURATION_MS = 30_000;

function resolveTheaterId(searchParams: URLSearchParams, pathname: string): string {
  const fromQuery = searchParams.get('theater_id') || '';
  if (fromQuery) return fromQuery;
  const pathParts = pathname.split('/');
  if (pathParts.length > 2 && pathParts[1] === 'obs') {
    return decodeURIComponent(pathParts[2]);
  }
  return '';
}

function appendJoinKey(url: string, joinKey: string): string {
  if (!joinKey || !url) return url;
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.origin !== window.location.origin) return url;
    if (!parsed.searchParams.has('join_key')) {
      parsed.searchParams.set('join_key', joinKey);
    }
    return parsed.pathname + parsed.search + parsed.hash;
  } catch {
    return url;
  }
}

function parseMusicVolume(rawVolume: string | null): number {
  const requestedVolume = Number(rawVolume ?? '0.8');
  return Number.isFinite(requestedVolume)
    ? Math.min(1, Math.max(0, requestedVolume))
    : 0.8;
}

function extractErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

function insertStampBeforeNonStamps(
  actions: ObsDoodleAnnotation[],
  stamp: ObsDoodleAnnotation,
): void {
  const firstNonStamp = actions.findIndex((action) => action.type !== 'stamp');
  if (firstNonStamp >= 0) {
    actions.splice(firstNonStamp, 0, stamp);
  } else {
    actions.push(stamp);
  }
}

function appendDrawBatch(
  actions: ObsDoodleAnnotation[],
  doodleCanvas: HTMLCanvasElement,
  renderSegment: (
    x0: number,
    y0: number,
    x1: number,
    y1: number,
    color: string,
    size: number,
  ) => void,
  points: readonly number[],
  color: string | undefined,
  size: number | undefined,
): void {
  const rect = doodleCanvas.getBoundingClientRect();
  const resolvedColor = color ?? '#ffffff';
  const resolvedSize = size || 3;
  for (let index = 0; index < points.length - 2; index += 2) {
    const action: ObsDoodleAnnotation = {
      type: 'draw',
      x0: points[index],
      y0: points[index + 1],
      x1: points[index + 2],
      y1: points[index + 3],
      color: resolvedColor,
      size: resolvedSize,
    };
    actions.push(action);
    renderSegment(
      action.x0! * rect.width,
      action.y0! * rect.height,
      action.x1! * rect.width,
      action.y1! * rect.height,
      resolvedColor,
      resolvedSize,
    );
  }
}

function preferredClipMimeType(): string {
  return (
    [
      'video/mp4;codecs=avc1.42E01E,mp4a.40.2',
      'video/webm;codecs=vp9,opus',
      'video/webm;codecs=vp8,opus',
      'video/webm',
    ].find((type) => MediaRecorder.isTypeSupported(type)) || ''
  );
}

function downloadClip(blob: Blob, mimeType: string): void {
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

async function createClipAudioMix(
  displayStream: MediaStream,
  includeMicrophone: boolean,
): Promise<AudioMix> {
  let microphoneStream: MediaStream | null = null;
  if (includeMicrophone) {
    const selectedDeviceId = localStorage.getItem('narratron_mic_device_id') || '';
    const audio: boolean | MediaTrackConstraints = selectedDeviceId
      ? { deviceId: { exact: selectedDeviceId } }
      : true;
    microphoneStream = await navigator.mediaDevices.getUserMedia({ audio, video: false });
  }
  const sources = [displayStream, microphoneStream].filter(
    (stream): stream is MediaStream => Boolean(stream && stream.getAudioTracks().length),
  );
  if (!sources.length) {
    return {
      tracks: [],
      cleanup: () => microphoneStream?.getTracks().forEach((track) => track.stop()),
    };
  }
  const audioContext = new AudioContext();
  const destination = audioContext.createMediaStreamDestination();
  const nodes = sources.map((stream) => {
    const node = audioContext.createMediaStreamSource(stream);
    node.connect(destination);
    return node;
  });
  await audioContext.resume();
  return {
    tracks: destination.stream.getAudioTracks(),
    cleanup: () => {
      nodes.forEach((node) => node.disconnect());
      microphoneStream?.getTracks().forEach((track) => track.stop());
      audioContext.close().catch(() => {});
    },
  };
}

function initializeClipMode(signal: AbortSignal): void {
  const clipCapturePanel = document.getElementById('clip-capture-panel');
  const clipCaptureStart = document.getElementById('clip-capture-start');
  const clipIncludeMic = document.getElementById('clip-include-mic') as HTMLInputElement | null;
  const clipRecordingBar = document.getElementById('clip-recording-bar');
  const clipRecordingTime = document.getElementById('clip-recording-time');
  const clipStartRecordingBtn = document.getElementById('clip-start-recording-btn');
  const clipStopEarlyBtn = document.getElementById('clip-stop-early-btn');
  const clipStartOverBtn = document.getElementById('clip-start-over-btn');
  const clipDraftHandoff = document.getElementById('clip-draft-handoff');
  const clipTikTokDraftLink = document.getElementById('clip-tiktok-draft-link');
  const clipDraftStatus = document.getElementById('clip-draft-status');

  let completedClip: Blob | null = null;
  let activeClipControl: ClipControl | null = null;
  let clipTimerInterval: ReturnType<typeof setInterval> | null = null;

  function setDraftStatus(message: string): void {
    if (clipDraftStatus) {
      clipDraftStatus.textContent = message;
    }
  }

  function showClipReadyState(message = 'Ready to record'): void {
    clipRecordingBar?.classList.add('active');
    clipRecordingBar?.classList.remove('recording');
    if (clipRecordingTime) {
      clipRecordingTime.textContent = message;
    }
  }

  function showTikTokDraftHandoff(clip: Blob): void {
    completedClip = clip;
    clipDraftHandoff?.classList.add('active');
  }

  async function sendClipToTikTokDraft(): Promise<void> {
    if (!completedClip) return;
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

    async function connected(event: MessageEvent): Promise<void> {
      const payload = event.data as { type?: string } | null;
      if (
        event.origin !== window.location.origin ||
        payload?.type !== 'narratron-tiktok-connected' ||
        !completedClip
      ) {
        return;
      }
      window.removeEventListener('message', connected);
      const clipToUpload = completedClip;
      try {
        setDraftStatus('Sending clip to TikTok…');
        const baseMimeType = (clipToUpload.type || 'video/webm').split(';', 1)[0];
        const response =
          baseMimeType === 'video/webm'
            ? await fetch('/api/tiktok/draft-upload-transcoded', {
                method: 'POST',
                body: (() => {
                  const form = new FormData();
                  form.append('clip', clipToUpload, 'narratron-clip.webm');
                  return form;
                })(),
              })
            : await fetch('/api/tiktok/draft-upload', {
                method: 'POST',
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
        if (baseMimeType !== 'video/webm') {
          const { upload_url } = (await response.json()) as TikTokUploadResponse;
          const put = await fetch(upload_url, {
            method: 'PUT',
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
        setDraftStatus('Sent. Checking TikTok delivery…');
        setTimeout(async () => {
          const status = (await fetch('/api/tiktok/draft-status').then((res) =>
            res.json(),
          )) as TikTokDraftStatusResponse;
          setDraftStatus(
            status.status === 'SEND_TO_USER_INBOX'
              ? 'Delivered to your TikTok inbox—open the notification to edit the draft.'
              : `TikTok status: ${status.status || 'processing'}${
                  status.fail_reason ? ` (${status.fail_reason})` : ''
                }`,
          );
        }, 4000);
      } catch (error) {
        setDraftStatus(extractErrorMessage(error, 'Unable to send the clip.'));
      }
    }

    window.addEventListener('message', connected, { signal });
  }

  async function recordClipFromObsStage(): Promise<Blob | null> {
    if (!navigator.mediaDevices?.getDisplayMedia || !window.MediaRecorder) {
      throw new Error('This browser cannot create a clip. Try a current desktop browser.');
    }
    const displayOptions: ObsDisplayMediaOptions = {
      video: { frameRate: { ideal: 30, max: 30 } },
      audio: true,
      selfBrowserSurface: 'include',
      preferCurrentTab: true,
    };
    const displayStream = await navigator.mediaDevices.getDisplayMedia(displayOptions);
    const videoTrack = displayStream.getVideoTracks()[0];
    displayStream.getAudioTracks().forEach((track) => track.stop());
    if (!videoTrack) {
      displayStream.getTracks().forEach((track) => track.stop());
      throw new Error('Select the Narratron Clip tab to capture it.');
    }

    const mimeType = preferredClipMimeType();
    let audioMix: AudioMix;
    try {
      audioMix = await createClipAudioMix(displayStream, Boolean(clipIncludeMic?.checked));
    } catch (error) {
      displayStream.getTracks().forEach((track) => track.stop());
      throw error;
    }

    const recorder = new MediaRecorder(
      new MediaStream([videoTrack, ...audioMix.tracks]),
      mimeType ? { mimeType } : undefined,
    );
    const chunks: BlobPart[] = [];
    let cancelled = false;
    let timerStartedAt = 0;

    const updateClipTimer = (): void => {
      const elapsed = Math.min(CLIP_DURATION_MS, Date.now() - timerStartedAt);
      if (clipRecordingTime) {
        clipRecordingTime.textContent = `0:${String(Math.ceil(elapsed / 1000)).padStart(2, '0')} / 0:30`;
      }
    };

    const finished = new Promise<Blob | null>((resolve, reject) => {
      recorder.addEventListener('dataavailable', (event: BlobEvent) => {
        if (event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener('error', () =>
        reject(new Error('The clip recorder stopped unexpectedly.')),
      );
      recorder.addEventListener(
        'stop',
        () => {
          displayStream.getTracks().forEach((track) => track.stop());
          audioMix.cleanup();
          activeClipControl = null;
          if (clipTimerInterval) clearInterval(clipTimerInterval);
          clipTimerInterval = null;
          showClipReadyState(cancelled ? 'Take discarded' : 'Recording complete');
          if (cancelled || !chunks.length) resolve(null);
          else resolve(new Blob(chunks, { type: recorder.mimeType || mimeType || 'video/webm' }));
        },
        { once: true },
      );
    });

    videoTrack.addEventListener(
      'ended',
      () => {
        cancelled = true;
        if (recorder.state !== 'inactive') recorder.stop();
      },
      { once: true },
    );

    clipCapturePanel?.classList.remove('active');
    activeClipControl = {
      stop: (discard = false): void => {
        cancelled = discard;
        if (recorder.state !== 'inactive') recorder.stop();
      },
    };
    timerStartedAt = Date.now();
    updateClipTimer();
    clipTimerInterval = setInterval(updateClipTimer, 250);
    clipRecordingBar?.classList.add('active', 'recording');
    recorder.start(250);
    setTimeout(() => {
      if (recorder.state !== 'inactive') recorder.stop();
    }, CLIP_DURATION_MS);
    return finished;
  }

  document.title = 'Narratron Clip Stage';
  clipCapturePanel?.classList.add('active');

  const beginClipCapture = async (): Promise<void> => {
    try {
      clipCapturePanel?.classList.remove('active');
      clipRecordingBar?.classList.remove('active', 'recording');
      const clip = await recordClipFromObsStage();
      if (!clip) return;
      downloadClip(clip, clip.type);
      showTikTokDraftHandoff(clip);
    } catch (error) {
      showClipReadyState(extractErrorMessage(error, 'Unable to record the clip.'));
      console.error('Failed to record OBS clip:', error);
    }
  };

  clipCaptureStart?.addEventListener('click', beginClipCapture, { signal });
  clipStartRecordingBtn?.addEventListener('click', beginClipCapture, { signal });
  clipStopEarlyBtn?.addEventListener('click', () => activeClipControl?.stop(false), { signal });
  clipStartOverBtn?.addEventListener('click', () => activeClipControl?.stop(true), { signal });
  clipTikTokDraftLink?.addEventListener('click', sendClipToTikTokDraft, { signal });
  signal.addEventListener('abort', () => {
    if (clipTimerInterval) {
      clearInterval(clipTimerInterval);
      clipTimerInterval = null;
    }
    activeClipControl?.stop(true);
  });
}

/** Initialize the UI-free OBS canvas stage, state/doodle WebSockets, music, captions, and clip mode. */
export async function initObsCanvas(): Promise<ObsCanvasController> {
  const urlParams = new URLSearchParams(window.location.search);
  const currentTheaterId = resolveTheaterId(urlParams, window.location.pathname);
  const currentJoinKey = urlParams.get('join_key') || '';
  const withJoinKey = (url: string): string => appendJoinKey(url, currentJoinKey);

  const rendererCanvas = document.getElementById('canvas-renderer') as HTMLCanvasElement | null;
  const doodleCanvas = document.getElementById('doodle-canvas') as HTMLCanvasElement | null;
  const currentImage = document.getElementById('current-image') as HTMLImageElement | null;
  const narrationMount = document.getElementById('narration-pane-mount');
  if (!rendererCanvas || !doodleCanvas || !currentImage || !narrationMount) {
    throw new Error('Required OBS canvas DOM elements are missing.');
  }

  const abortController = new AbortController();
  const { signal } = abortController;

  const narrationPane = await createNarrationPane(narrationMount);
  narrationPane.setDrawingColorSelected(false);

  const audioEnabled = urlParams.get('audio') !== '0';
  const { updateSceneDialogue, enqueueSceneSpeech } = createCanvasScene({
    narrationPane,
    resolveUrl: withJoinKey,
    audioEnabled: () => audioEnabled,
  });

  let lastTime = 0;
  let lastImageRawUrl: string | undefined = '';
  let lastImageEffect: string | null = null;
  let isFirstCanvasLoad = true;
  let lastAnimationSignature = '';
  let doodleActions: ObsDoodleAnnotation[] = [];
  let doodlesEnabled = true;
  let displayedDoodlePageId: string | null = null;
  let doodlePageRequestSequence = 0;

  // OBS Browser Sources capture audio produced by this page. Keep this
  // player deliberately headless so the visual output remains clean.
  const musicVolume = parseMusicVolume(urlParams.get('audio_volume'));
  const bgMusic = new Audio();
  bgMusic.volume = musicVolume;
  let activePlaylistName: string | null = null;
  let activeTracks: readonly string[] = [];
  let activeTrackSrc: string | null = null;
  let lastMusicTime = 0;
  let hasReceivedMusicState = false;
  let latestMusicState: ObsMusicState | null = null;
  let overlayAudioPaused = false;

  function chooseTrack(tracks: readonly string[]): string {
    if (tracks.length === 1) return tracks[0];
    const alternatives = tracks.filter((track) => track !== activeTrackSrc);
    return alternatives[Math.floor(Math.random() * alternatives.length)];
  }

  function playNextTrack(): void {
    if (!audioEnabled || activeTracks.length === 0) return;
    const track = chooseTrack(activeTracks);
    activeTrackSrc = track;
    bgMusic.src = withJoinKey(track);
    bgMusic.play().catch((error: unknown) => {
      console.warn('OBS music playback could not start:', error);
    });
  }

  function syncMusic(music: ObsMusicState | null | undefined): void {
    latestMusicState = music ?? null;
    if (!audioEnabled || overlayAudioPaused) return;

    const playlist = music?.playlist;
    const tracks = Array.isArray(music?.tracks) ? music.tracks : [];
    if (!playlist || tracks.length === 0) {
      activePlaylistName = null;
      activeTracks = [];
      activeTrackSrc = null;
      bgMusic.pause();
      bgMusic.removeAttribute('src');
      return;
    }

    const playlistChanged = playlist !== activePlaylistName;
    activePlaylistName = playlist;
    activeTracks = tracks;
    if (playlistChanged || !activeTrackSrc || !tracks.includes(activeTrackSrc)) {
      playNextTrack();
    }

    if (music.paused) {
      bgMusic.pause();
    } else if (bgMusic.paused) {
      bgMusic.play().catch((error: unknown) => {
        console.warn('OBS music playback could not resume:', error);
      });
    }
  }

  bgMusic.addEventListener('ended', () => playNextTrack(), { signal });

  // There is deliberately no player UI in the OBS view. A normal web
  // browser may still require a user activation before it permits audio;
  // a click anywhere on the canvas retries the already-selected track.
  // OBS Browser Source normally permits autoplay and needs no click.
  document.addEventListener(
    'pointerdown',
    () => {
      if (latestMusicState) syncMusic(latestMusicState);
    },
    { signal },
  );

  // When embedded by Foundry, the iframe receives keyboard focus and
  // its events cannot bubble to the host. Relay the module's close
  // shortcut back to the parent without affecting standalone OBS use.
  window.addEventListener(
    'keydown',
    (event: KeyboardEvent) => {
      if (event.altKey && event.code === 'KeyO' && window.parent !== window) {
        event.preventDefault();
        window.parent.postMessage(
          {
            source: 'narratron-buddy-obs',
            action: 'hide-overlay',
          },
          '*',
        );
      }
    },
    { signal },
  );

  window.addEventListener(
    'message',
    (event: MessageEvent) => {
      const payload = event.data as FoundryMessage | null;
      if (payload?.source !== 'narratron-buddy-foundry') return;

      if (payload.action === 'pause-audio') {
        overlayAudioPaused = true;
        bgMusic.pause();
      } else if (payload.action === 'resume-audio') {
        overlayAudioPaused = false;
        if (latestMusicState) syncMusic(latestMusicState);
      }
    },
    { signal },
  );

  const imageRenderer: ImageRenderer = createImageRenderer({
    canvas: rendererCanvas,
    image: currentImage,
    fadeDuration: 1200,
    fitMode: 'cover',
  });
  const doodleRenderer = createDoodleRenderer({
    canvas: doodleCanvas,
    fitMode: 'cover',
    isVisible: () => doodlesEnabled,
    stampLayer: document.getElementById('canvas-stamp-layer'),
    canSelectSegment: () => false,
  });

  const resizeRendererCanvas = imageRenderer.resize;
  const applyImageTransition = imageRenderer.applyTransition;
  const resizeDoodleCanvas = (): void => doodleRenderer.resize(doodleActions);
  const redrawAllDoodles = (): void => doodleRenderer.redraw(doodleActions);
  const renderSegment = doodleRenderer.renderSegment;
  const renderTextAnnotation = doodleRenderer.renderText;

  window.addEventListener(
    'resize',
    () => {
      resizeRendererCanvas();
      resizeDoodleCanvas();
    },
    { signal },
  );

  function bringStampToFront(stampId: string | undefined): void {
    if (!stampId) return;
    const index = doodleActions.findIndex(
      (existing) => existing.type === 'stamp' && existing.id === stampId,
    );
    if (index >= 0) {
      const [selected] = doodleActions.splice(index, 1);
      insertStampBeforeNonStamps(doodleActions, selected);
    }
    const stampLayer = document.getElementById('canvas-stamp-layer');
    if (stampLayer) {
      const el = Array.from(stampLayer.children).find(
        (node) => (node as HTMLElement).dataset.annotationId === stampId,
      );
      if (el && stampLayer.lastElementChild !== el) {
        stampLayer.appendChild(el);
      }
    }
  }

  function applyDoodleSnapshot(data: DoodleSnapshotPayload): void {
    if (data.page_id && displayedDoodlePageId && data.page_id !== displayedDoodlePageId) return;
    displayedDoodlePageId = data.page_id || displayedDoodlePageId;
    if (data.enabled !== undefined) doodlesEnabled = Boolean(data.enabled);
    doodleActions = [];
    redrawAllDoodles();
    (data.stamps || []).forEach((action) => {
      doodleActions.push(action);
    });
    (data.batches || []).forEach((batch) => {
      appendDrawBatch(
        doodleActions,
        doodleCanvas!,
        renderSegment,
        batch.points || [],
        batch.color,
        batch.size,
      );
    });
    (data.annotations || []).forEach((action) => {
      doodleActions.push(action);
      renderTextAnnotation(action);
    });
    redrawAllDoodles();
  }

  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  let ws: WebSocket | null = null;

  async function requestPageDoodles(pageId: string | null = displayedDoodlePageId): Promise<void> {
    if (!pageId) return;
    const sequence = ++doodlePageRequestSequence;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'subscribe_doodles', page_id: pageId }));
      return;
    }
    try {
      const params = new URLSearchParams({ theater_id: currentTheaterId, page_id: pageId });
      const response = await fetch(withJoinKey('/api/doodles?' + params));
      if (!response.ok) throw new Error(`Unable to load page doodles (${response.status}).`);
      const snapshot = (await response.json()) as DoodleSnapshotPayload;
      if (sequence === doodlePageRequestSequence) applyDoodleSnapshot(snapshot);
    } catch (error) {
      console.warn('Could not load page doodles.', error);
    }
  }

  if (currentTheaterId) {
    const doodleParams = new URLSearchParams();
    doodleParams.set('theater_id', currentTheaterId);
    if (currentJoinKey) doodleParams.set('join_key', currentJoinKey);
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/doodle?${doodleParams.toString()}`;
    ws = new WebSocket(wsUrl);
    ws.onopen = () => {
      if (displayedDoodlePageId) void requestPageDoodles();
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      const data = JSON.parse(event.data) as DoodleSocketMessage;
      if (data.page_id && displayedDoodlePageId && data.page_id !== displayedDoodlePageId) return;
      if (data.type === 'doodles_toggle') {
        doodlesEnabled = Boolean(data.enabled);
        redrawAllDoodles();
      } else if (data.type === 'clear') {
        doodleActions = [];
        redrawAllDoodles();
      } else if (data.type === 'doodle_snapshot') {
        doodlePageRequestSequence += 1;
        applyDoodleSnapshot(data);
      } else if (data.type === 'draw_batch') {
        appendDrawBatch(
          doodleActions,
          doodleCanvas,
          renderSegment,
          data.points || [],
          data.color,
          data.size,
        );
      } else if (data.type === 'draw') {
        const drawAction: ObsDoodleAnnotation = {
          ...data,
          type: 'draw',
        };
        doodleActions.push(drawAction);
        const rect = doodleCanvas.getBoundingClientRect();
        renderSegment(
          (drawAction.x0 ?? 0) * rect.width,
          (drawAction.y0 ?? 0) * rect.height,
          (drawAction.x1 ?? 0) * rect.width,
          (drawAction.y1 ?? 0) * rect.height,
          drawAction.color ?? '#ffffff',
          drawAction.size || 3,
        );
      } else if (data.type === 'text') {
        const textAction: ObsDoodleAnnotation = {
          ...data,
          type: 'text',
        };
        const index = doodleActions.findIndex(
          (action) => action.type === 'text' && action.id === textAction.id,
        );
        if (index >= 0) doodleActions[index] = textAction;
        else doodleActions.push(textAction);
        redrawAllDoodles();
      } else if (data.type === 'stamp') {
        const stampAction: ObsDoodleAnnotation = {
          ...data,
          type: 'stamp',
        };
        const index = doodleActions.findIndex(
          (action) =>
            action.type === 'stamp' &&
            ((action.id && action.id === stampAction.id) ||
              (String(action.stamp_id) === String(stampAction.stamp_id) &&
                String(action.user_id) === String(stampAction.user_id))),
        );
        if (index >= 0) doodleActions.splice(index, 1);
        insertStampBeforeNonStamps(doodleActions, stampAction);
        redrawAllDoodles();
      } else if (data.type === 'select_stamp') {
        bringStampToFront(data.id);
      } else if (data.type === 'remove_stamp') {
        const index = doodleActions.findIndex(
          (action) => action.type === 'stamp' && action.id === data.id,
        );
        if (index >= 0) {
          doodleActions.splice(index, 1);
          redrawAllDoodles();
        }
      }
    };
  }

  let latestStateRequestSequence = 0;

  async function fetchLatestImage(): Promise<void> {
    if (!currentTheaterId || signal.aborted) return;
    const requestSequence = ++latestStateRequestSequence;
    try {
      const apiParams = new URLSearchParams();
      apiParams.set('theater_id', currentTheaterId);
      if (currentJoinKey) apiParams.set('join_key', currentJoinKey);
      const apiUrl = `/api/latest?${apiParams.toString()}`;
      const response = await fetch(apiUrl);
      if (!response.ok) throw new Error(`Unable to load canvas state (${response.status}).`);
      const data = (await response.json()) as ObsLatestResponse;
      if (requestSequence !== latestStateRequestSequence || signal.aborted) return;

      updateSceneDialogue(
        data.scene_dialogue,
        data.narration,
        data.narration_spans,
        data.committed_scene_speech_generation,
      );

      if (data.music && (!hasReceivedMusicState || (data.music.time ?? 0) > lastMusicTime)) {
        lastMusicTime = data.music.time ?? 0;
        hasReceivedMusicState = true;
        syncMusic(data.music);
      }

      const cursor = data.orator_cursor;
      const doodlePage = typeof cursor === 'number' ? data.history?.[cursor] : undefined;
      const pageId = data.doodle_page_id || doodlePage?.page_id || null;
      if (pageId !== displayedDoodlePageId) {
        displayedDoodlePageId = pageId;
        doodleActions = [];
        redrawAllDoodles();
        void requestPageDoodles(pageId);
      }
      if (data.doodles_enabled !== undefined && data.doodles_enabled !== doodlesEnabled) {
        doodlesEnabled = Boolean(data.doodles_enabled);
        redrawAllDoodles();
      }

      const currentImageEffect = data.effect || 'gleam3';
      const animationSignature = getAnimationSignature(data.animation);
      const isNewImage =
        Boolean(data.latest || animationSignature) &&
        ((data.time ?? 0) > lastTime ||
          data.latest !== lastImageRawUrl ||
          currentImageEffect !== lastImageEffect ||
          animationSignature !== lastAnimationSignature);
      const firstLoad = isFirstCanvasLoad;
      isFirstCanvasLoad = false;
      if (!isNewImage) return;

      lastTime = data.time ?? 0;
      lastImageRawUrl = data.latest;
      lastImageEffect = currentImageEffect;
      lastAnimationSignature = animationSignature;
      if (animationSignature && data.animation) {
        playCanvasAnimation(imageRenderer, data.animation, withJoinKey);
      } else if (data.latest) {
        imageRenderer.stopSequence();
        const newImgUrl = withJoinKey(
          data.latest + (data.latest.includes('?') ? '&t=' : '?t=') + Date.now(),
        );
        const transition = firstLoad
          ? 'none'
          : (data.transition || 'crossfade').toLowerCase().trim();
        void applyImageTransition(newImgUrl, transition, currentImageEffect);
      }
    } catch (err) {
      console.error('OBS Canvas fetch error:', err);
    }
  }

  resizeRendererCanvas();
  resizeDoodleCanvas();
  void fetchLatestImage();

  // Keep image/doodle collaboration concerns separate. This socket only
  // says when REST state is stale; it does not carry render data.
  let canvasStateWs: WebSocket | null = null;
  let canvasStateReconnectDelay = 1000;
  let canvasStateReconnectTimer: ReturnType<typeof setTimeout> | null = null;

  function connectCanvasStateSocket(): void {
    if (!currentTheaterId || signal.aborted) return;
    const stateParams = new URLSearchParams();
    stateParams.set('theater_id', currentTheaterId);
    if (currentJoinKey) stateParams.set('join_key', currentJoinKey);
    const stateWsUrl = `${wsProtocol}//${window.location.host}/ws/canvas-state?${stateParams.toString()}`;
    canvasStateWs = new WebSocket(stateWsUrl);
    canvasStateWs.onopen = () => {
      canvasStateReconnectDelay = 1000;
    };
    canvasStateWs.onmessage = (event: MessageEvent<string>) => {
      try {
        const message = JSON.parse(event.data) as CanvasStateSocketMessage;
        if (
          message.type === 'state_ready' ||
          (message.type === 'state_changed' && message.domains?.includes('latest'))
        ) {
          void fetchLatestImage();
        } else if (
          message.type === 'scene_speech_ready' &&
          message.audio_url &&
          typeof message.speech_generation === 'number'
        ) {
          enqueueSceneSpeech(message.audio_url, message.speech_generation);
        }
      } catch (error) {
        console.warn('Ignoring invalid canvas-state message.', error);
      }
    };
    canvasStateWs.onclose = () => {
      canvasStateWs = null;
      if (!signal.aborted && !canvasStateReconnectTimer) {
        canvasStateReconnectTimer = setTimeout(() => {
          canvasStateReconnectTimer = null;
          connectCanvasStateSocket();
        }, canvasStateReconnectDelay);
        canvasStateReconnectDelay = Math.min(canvasStateReconnectDelay * 2, 30000);
      }
    };
  }

  startCanvasStatePolling(fetchLatestImage);
  connectCanvasStateSocket();

  // Clip mode is intentionally isolated from the interactive canvas so
  // screen capture sees the OBS visual stage only. The recorder gets a
  // filtered video-only stream; it cannot receive microphone/tab audio.
  if (urlParams.get('clip') === '1') {
    initializeClipMode(signal);
  }

  return {
    fetchLatestImage,
    syncMusic,
    dispose(): void {
      abortController.abort();
      if (canvasStateReconnectTimer) {
        clearTimeout(canvasStateReconnectTimer);
        canvasStateReconnectTimer = null;
      }
      ws?.close();
      canvasStateWs?.close();
      bgMusic.pause();
      bgMusic.removeAttribute('src');
      imageRenderer.stopSequence();
    },
  };
}
