interface StageDisplayOptions extends DisplayMediaStreamOptions {
  readonly selfBrowserSurface: 'include';
  readonly preferCurrentTab: true;
}

/** Called by the server's isolated Chromium tab after the OBS stage is ready. */
export async function startObsStageRecording(): Promise<{ stop(): Promise<string> }> {
  const options: StageDisplayOptions = {
    video: { frameRate: { ideal: 30, max: 30 } },
    audio: true,
    selfBrowserSurface: 'include',
    preferCurrentTab: true,
  };
  const stream = await navigator.mediaDevices.getDisplayMedia(options);
  const closeTracks = (): void => stream.getTracks().forEach((track) => track.stop());
  try {
    if (stream.getVideoTracks()[0]?.getSettings().displaySurface !== 'browser') {
      throw new Error('The OBS browser tab could not be captured.');
    }
    const recorder = new MediaRecorder(stream, {
      mimeType: 'video/webm;codecs=vp8,opus',
      videoBitsPerSecond: 2_000_000,
    });
    const chunks: Blob[] = [];
    recorder.addEventListener('dataavailable', (event) => {
      if (event.data.size) chunks.push(event.data);
    });
    recorder.start(250);
    const finished = new Promise<Blob>((resolve) => {
      recorder.addEventListener('stop', () => {
        closeTracks();
        resolve(new Blob(chunks, { type: 'video/webm' }));
      }, { once: true });
    });
    return {
      async stop(): Promise<string> {
        if (recorder.state !== 'inactive') recorder.stop();
        const clip = await finished;
        if (!clip.size) throw new Error('The OBS recording is empty.');
        return new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
          reader.onerror = () => reject(reader.error);
          reader.readAsDataURL(clip);
        });
      },
    };
  } catch (error) {
    closeTracks();
    throw error;
  }
}
