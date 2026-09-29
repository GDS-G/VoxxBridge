import { App, type McpUiToolResultNotification } from "@modelcontextprotocol/ext-apps";
import "./styles.css";

type ToolResult = McpUiToolResultNotification["params"];
type ResultContent = NonNullable<ToolResult["content"]>[number];
type ResourceLink = Extract<ResultContent, { type: "resource_link" }>;
type AudioContent = Extract<ResultContent, { type: "audio" }>;
type ResourceReadResult = Awaited<ReturnType<App["readServerResource"]>>;
type ResourceReadContent = ResourceReadResult["contents"][number];
type BlobResourceContents = Extract<ResourceReadContent, { blob: string }>;
type DeliveryMode = "playback" | "file" | "both";
type PlaybackState = "unavailable" | "ready" | "decoding" | "playing" | "paused" | "ended";

type SpeechMetadata = {
  app_resource_playback?: boolean;
  delivery?: DeliveryMode;
  download_expires_at?: string;
  file_mime_type?: string;
  file_name?: string;
  file_size_bytes?: number;
  inline_audio_included?: boolean;
  mime_type?: string;
  playback_requested?: boolean;
  provider?: string;
};

const app = new App(
  { name: "VoxBridge Audio Delivery", version: "0.2.0" },
  { availableDisplayModes: ["inline"] },
  { autoResize: true },
);

const summary = requiredElement<HTMLParagraphElement>("summary");
const details = requiredElement<HTMLDListElement>("details");
const mode = requiredElement<HTMLElement>("mode");
const fileName = requiredElement<HTMLElement>("file-name");
const format = requiredElement<HTMLElement>("format");
const fileSize = requiredElement<HTMLElement>("file-size");
const expiryRow = requiredElement<HTMLElement>("expiry-row");
const expiry = requiredElement<HTMLElement>("expiry");
const playback = requiredElement<HTMLElement>("playback");
const playPauseButton = requiredElement<HTMLButtonElement>("play-pause");
const playbackIcon = requiredElement<HTMLElement>("playback-icon");
const playbackLabel = requiredElement<HTMLElement>("playback-label");
const actions = requiredElement<HTMLElement>("actions");
const downloadButton = requiredElement<HTMLButtonElement>("download");
const buttonLabel = downloadButton.querySelector<HTMLElement>(".button-label");
const status = requiredElement<HTMLParagraphElement>("status");

if (!buttonLabel) {
  throw new Error("Missing download button label");
}

const resolvedButtonLabel = buttonLabel;

let currentResource: ResourceLink | undefined;
let isConnected = false;
let hostCanDownload = false;
let hostCanReadResources = false;
let isDownloading = false;
let latestResult: ToolResult | undefined;
let resultRevision = 0;
let playbackRevision = 0;
let playbackBytes: ArrayBuffer | undefined;
let playbackContext: AudioContext | undefined;
let playbackBuffer: AudioBuffer | undefined;
let playbackSource: AudioBufferSourceNode | undefined;
let playbackState: PlaybackState = "unavailable";
let playbackOffsetSeconds = 0;
let playbackStartedAt = 0;

function requiredElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing required element: ${id}`);
  }
  return element as T;
}

function setStatus(message: string, kind: "neutral" | "success" | "error" = "neutral") {
  status.textContent = message;
  status.dataset.kind = kind;
  status.setAttribute("role", kind === "error" ? "alert" : "status");
}

function setDownloading(value: boolean) {
  isDownloading = value;
  downloadButton.disabled = value || !hostCanDownload;
  downloadButton.setAttribute("aria-busy", String(value));
  resolvedButtonLabel.textContent = value ? "Preparing download…" : "Download audio";
}

function parseMetadata(content: ToolResult["content"]): SpeechMetadata {
  for (const item of content ?? []) {
    if (item.type !== "text") continue;
    try {
      const value: unknown = JSON.parse(item.text);
      if (value && typeof value === "object" && !Array.isArray(value)) {
        return value as SpeechMetadata;
      }
    } catch {
      // A text block may be a human-readable message rather than metadata.
    }
  }
  return {};
}

function findResourceLink(content: ToolResult["content"]): ResourceLink | undefined {
  return (content ?? []).find(
    (item): item is ResourceLink =>
      item.type === "resource_link" && item.uri.startsWith("voxbridge://audio/"),
  );
}

function findAudioContent(content: ToolResult["content"]): AudioContent | undefined {
  return (content ?? []).find((item): item is AudioContent => item.type === "audio");
}

function isBlobResourceContents(content: ResourceReadContent): content is BlobResourceContents {
  return "blob" in content && typeof content.blob === "string";
}

function decodeBase64Audio(data: string, mimeType: string): ArrayBuffer {
  const base64Pattern = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
  if (!mimeType.startsWith("audio/") || data.length === 0 || !base64Pattern.test(data)) {
    throw new Error("Invalid base64 audio content");
  }

  const decoded = atob(data);
  if (decoded.length === 0) {
    throw new Error("Empty audio content");
  }

  const bytes = new Uint8Array(decoded.length);
  for (let index = 0; index < decoded.length; index += 1) {
    bytes[index] = decoded.charCodeAt(index);
  }
  return bytes.buffer;
}

function parseResourceAudio(result: ResourceReadResult, resource: ResourceLink) {
  if (result.contents.length !== 1) {
    throw new Error("Expected exactly one resource content item");
  }

  const content = result.contents[0];
  if (
    !content ||
    !isBlobResourceContents(content) ||
    content.uri !== resource.uri ||
    !content.mimeType?.startsWith("audio/")
  ) {
    throw new Error("Expected one base64 audio blob resource");
  }

  return { data: content.blob, mimeType: content.mimeType };
}

function stopPlaybackSource() {
  const source = playbackSource;
  playbackSource = undefined;
  if (!source) return;

  source.onended = null;
  try {
    source.stop();
  } catch {
    // The source may already have ended.
  }
  try {
    source.disconnect();
  } catch {
    // The source may already have been disconnected.
  }
}

function closePlaybackContext(context: AudioContext | undefined) {
  if (!context || context.state === "closed") return;
  void context.close().catch(() => {
    // Closing is best-effort during replacement or teardown.
  });
}

function clearPlayback() {
  playbackRevision += 1;
  stopPlaybackSource();
  const context = playbackContext;
  playbackContext = undefined;
  playbackBytes = undefined;
  playbackBuffer = undefined;
  playbackState = "unavailable";
  playbackOffsetSeconds = 0;
  playbackStartedAt = 0;
  closePlaybackContext(context);
  playback.hidden = true;
  playPauseButton.disabled = false;
  playPauseButton.setAttribute("aria-busy", "false");
  playPauseButton.setAttribute("aria-pressed", "false");
  playbackIcon.textContent = "▶";
  playbackLabel.textContent = "Play audio";
}

function preparePlayback(audio: Pick<AudioContent, "data" | "mimeType"> | undefined) {
  clearPlayback();
  if (!audio) return false;

  try {
    playbackBytes = decodeBase64Audio(audio.data, audio.mimeType);
    playbackState = "ready";
    playback.hidden = false;
    return true;
  } catch {
    playbackBytes = undefined;
    return false;
  }
}

function deriveMode(metadata: SpeechMetadata, content: ToolResult["content"]): DeliveryMode {
  if (metadata.delivery === "playback" || metadata.delivery === "file" || metadata.delivery === "both") {
    return metadata.delivery;
  }
  const hasAudio = (content ?? []).some((item) => item.type === "audio");
  const hasFile = Boolean(findResourceLink(content));
  if (hasFile && metadata.app_resource_playback === true) return "both";
  if (hasFile && metadata.playback_requested === false) return "file";
  return hasAudio && hasFile ? "both" : hasFile ? "file" : "playback";
}

function modeLabel(value: DeliveryMode) {
  if (value === "both") return "Playback + file";
  return value === "file" ? "File only" : "Playback only";
}

function formatBytes(value: number | undefined) {
  if (value === undefined || !Number.isFinite(value) || value < 0) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB"];
  let amount = value / 1024;
  let unit = units[0];
  for (let index = 1; amount >= 1024 && index < units.length; index += 1) {
    amount /= 1024;
    unit = units[index];
  }
  return `${amount >= 10 ? amount.toFixed(1) : amount.toFixed(2)} ${unit}`;
}

function formatExpiry(value: string | undefined) {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function fileDeliveryStatus() {
  return hostCanDownload
    ? "The audio file remains ready to download."
    : "Use the host's attached-file Download action below.";
}

function showPlaybackUnavailable(reason: string) {
  clearPlayback();
  summary.textContent = "Your downloadable audio file is ready, but playback is unavailable.";
  setStatus(`${reason} ${fileDeliveryStatus()}`, "error");
}

async function preparePlaybackFromResource(resource: ResourceLink, revision: number) {
  if (!isConnected || !hostCanReadResources) return;

  setStatus(
    hostCanDownload
      ? "The file is ready. Preparing playback…"
      : "Preparing playback. The host's attached-file Download action remains available below.",
  );

  try {
    const result = await app.readServerResource({ uri: resource.uri });
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;

    const resourceAudio = parseResourceAudio(result, resource);
    if (!preparePlayback(resourceAudio)) {
      throw new Error("The resource did not contain valid base64 audio");
    }

    summary.textContent = "Your audio is ready to play and download.";
    setStatus(
      hostCanDownload
        ? "Ready to play or download."
        : "Ready to play. Use the host's attached-file Download action below.",
    );
  } catch {
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    showPlaybackUnavailable(
      "Playback is unavailable because the temporary audio resource could not be read.",
    );
  }
}

function renderResult(result: ToolResult) {
  latestResult = result;
  const revision = ++resultRevision;
  clearPlayback();
  if (result.isError) {
    currentResource = undefined;
    actions.hidden = true;
    details.hidden = true;
    summary.textContent = "Audio generation did not complete.";
    setStatus("The speech tool returned an error.", "error");
    return;
  }

  const metadata = parseMetadata(result.content);
  const resource = findResourceLink(result.content);
  const audio = findAudioContent(result.content);
  const delivery = deriveMode(metadata, result.content);
  const expiresAt = formatExpiry(metadata.download_expires_at);

  currentResource = resource;
  details.hidden = false;
  mode.textContent = modeLabel(delivery);
  fileName.textContent = resource?.name ?? metadata.file_name ?? "No file included";
  format.textContent = resource?.mimeType ?? metadata.file_mime_type ?? metadata.mime_type ?? "—";
  fileSize.textContent = formatBytes(resource?.size ?? metadata.file_size_bytes);
  expiryRow.hidden = !expiresAt;
  expiry.textContent = expiresAt ?? "—";

  if (delivery === "both") {
    summary.textContent = "Your audio is ready to play and download.";
  } else if (delivery === "file") {
    summary.textContent = "Your downloadable audio file is ready.";
  } else {
    summary.textContent = "Your audio is ready for playback.";
  }

  actions.hidden = !resource || !hostCanDownload;
  setDownloading(false);

  if (delivery === "playback") {
    if (preparePlayback(audio)) {
      setStatus("Ready to play. Playback mode does not include a downloadable file.");
    } else {
      setStatus("The inline audio could not be prepared for playback.", "error");
    }
    return;
  }

  if (delivery === "both") {
    if (!resource) {
      summary.textContent = "Audio generation did not include a downloadable file.";
      setStatus("Playback and file delivery could not be prepared.", "error");
      return;
    }
    if (!isConnected) {
      setStatus(
        "Waiting for the host connection before preparing playback. The attached audio file remains available below.",
      );
      return;
    }
    if (!hostCanReadResources) {
      showPlaybackUnavailable(
        "Playback is unavailable because this host does not support server resource reads.",
      );
      return;
    }
    void preparePlaybackFromResource(resource, revision);
    return;
  }

  if (!resource) {
    setStatus("The generated audio did not include a downloadable file.", "error");
  } else if (!hostCanDownload) {
    setStatus("Use the host's attached-file Download action below.");
  } else {
    setStatus("Ready to download.");
  }
}

function pausePlayback() {
  if (
    playbackState !== "playing" ||
    !playbackContext ||
    !playbackBuffer ||
    !playbackSource
  ) {
    return;
  }

  const elapsed = Math.max(0, playbackContext.currentTime - playbackStartedAt);
  playbackOffsetSeconds = Math.min(
    playbackBuffer.duration,
    playbackOffsetSeconds + elapsed,
  );
  stopPlaybackSource();
  playbackState = playbackOffsetSeconds >= playbackBuffer.duration ? "ended" : "paused";
  playPauseButton.setAttribute("aria-pressed", "false");
  playbackIcon.textContent = "▶";
  playbackLabel.textContent = playbackState === "ended" ? "Play again" : "Play audio";
  if (playbackState === "ended") {
    setStatus("Playback complete.", "success");
  } else {
    setStatus("Playback paused.");
  }
}

async function togglePlayback() {
  if (playbackState === "playing") {
    pausePlayback();
    return;
  }

  if (!playbackBytes || playbackState === "decoding") return;

  const revision = playbackRevision;
  const bytes = playbackBytes;
  const previousState = playbackState;
  if (previousState === "ended") {
    playbackOffsetSeconds = 0;
  }
  playbackState = "decoding";
  playPauseButton.disabled = true;
  playPauseButton.setAttribute("aria-busy", "true");
  setStatus("Preparing playback…");

  let context = playbackContext;
  try {
    if (!context || context.state === "closed") {
      context = new AudioContext();
      playbackContext = context;
    }

    const resumePromise = context.state === "suspended" ? context.resume() : Promise.resolve();
    const decodePromise: Promise<AudioBuffer> = playbackBuffer
      ? Promise.resolve(playbackBuffer)
      : context.decodeAudioData(bytes.slice(0));
    const [, decodedBuffer] = await Promise.all([resumePromise, decodePromise]);

    if (
      revision !== playbackRevision ||
      playbackBytes !== bytes ||
      playbackContext !== context
    ) {
      return;
    }
    if (!Number.isFinite(decodedBuffer.duration) || decodedBuffer.duration <= 0) {
      throw new Error("Decoded audio is empty");
    }

    playbackBuffer = decodedBuffer;
    if (playbackOffsetSeconds >= decodedBuffer.duration) {
      playbackOffsetSeconds = 0;
    }

    const source = context.createBufferSource();
    source.buffer = decodedBuffer;
    source.connect(context.destination);
    playbackSource = source;
    source.onended = () => {
      if (revision !== playbackRevision || playbackSource !== source) return;
      playbackSource = undefined;
      source.disconnect();
      playbackOffsetSeconds = 0;
      playbackState = "ended";
      playPauseButton.disabled = false;
      playPauseButton.setAttribute("aria-busy", "false");
      playPauseButton.setAttribute("aria-pressed", "false");
      playbackIcon.textContent = "▶";
      playbackLabel.textContent = "Play again";
      setStatus("Playback complete.", "success");
    };
    playbackStartedAt = context.currentTime;
    source.start(0, playbackOffsetSeconds);
    playbackState = "playing";
    playPauseButton.disabled = false;
    playPauseButton.setAttribute("aria-busy", "false");
    playPauseButton.setAttribute("aria-pressed", "true");
    playbackIcon.textContent = "❚❚";
    playbackLabel.textContent = "Pause audio";
    setStatus("Playing audio.");
  } catch {
    if (revision !== playbackRevision || playbackBytes !== bytes) return;
    stopPlaybackSource();
    if (playbackContext === context) {
      playbackContext = undefined;
    }
    closePlaybackContext(context);
    playbackState = previousState === "paused" ? "paused" : previousState === "ended" ? "ended" : "ready";
    playPauseButton.disabled = false;
    playPauseButton.setAttribute("aria-busy", "false");
    playPauseButton.setAttribute("aria-pressed", "false");
    playbackIcon.textContent = "▶";
    playbackLabel.textContent = playbackState === "ended" ? "Play again" : "Play audio";
    const fallback = currentResource ? ` ${fileDeliveryStatus()}` : "";
    setStatus(
      `Playback could not start because Web Audio was unavailable or could not decode the audio.${fallback}`,
      "error",
    );
  }
}

async function downloadCurrentResource() {
  if (isDownloading || !currentResource) return;
  if (!hostCanDownload) {
    setStatus("This host does not support native file downloads.", "error");
    return;
  }

  const resource = currentResource;
  const revision = resultRevision;
  setDownloading(true);
  setStatus("Waiting for download confirmation…");
  try {
    const result = await app.downloadFile({ contents: [resource] });
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    if (result.isError) {
      setStatus("Download cancelled or declined by the host.");
      return;
    }
    setStatus("Download started successfully.", "success");
  } catch {
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    setStatus("Download failed. The temporary audio file may have expired.", "error");
  } finally {
    if (revision === resultRevision && currentResource?.uri === resource.uri) {
      setDownloading(false);
    }
  }
}

downloadButton.addEventListener("click", () => {
  void downloadCurrentResource();
});

playPauseButton.addEventListener("click", () => {
  void togglePlayback();
});

app.addEventListener("toolinput", (params) => {
  const requested = params.arguments?.delivery;
  if (requested === "playback" || requested === "file" || requested === "both") {
    mode.textContent = modeLabel(requested);
  }
  resultRevision += 1;
  latestResult = undefined;
  currentResource = undefined;
  clearPlayback();
  setDownloading(false);
  actions.hidden = true;
  summary.textContent = "Generating audio…";
  setStatus("Waiting for the speech provider.");
});

app.addEventListener("toolresult", renderResult);

app.addEventListener("toolcancelled", (params) => {
  resultRevision += 1;
  latestResult = undefined;
  currentResource = undefined;
  clearPlayback();
  setDownloading(false);
  actions.hidden = true;
  summary.textContent = "Audio generation was cancelled.";
  setStatus(params.reason ? `Cancelled: ${params.reason}` : "Cancelled.");
});

app.onteardown = () => {
  resultRevision += 1;
  currentResource = undefined;
  clearPlayback();
  return {};
};

window.addEventListener("pagehide", () => {
  resultRevision += 1;
  currentResource = undefined;
  clearPlayback();
});

try {
  await app.connect();
  isConnected = true;
  const hostCapabilities = app.getHostCapabilities();
  hostCanDownload = hostCapabilities?.downloadFile !== undefined;
  hostCanReadResources = hostCapabilities?.serverResources !== undefined;
  downloadButton.disabled = !hostCanDownload;
  if (latestResult) {
    renderResult(latestResult);
  } else {
    setStatus(
      hostCanDownload
        ? "Connected. Waiting for generated audio…"
        : "Connected. File results will use the host's attached-file Download action.",
    );
  }
} catch (error) {
  const message = error instanceof Error ? error.message : "Unknown error";
  setStatus(`Could not connect to the host: ${message}`, "error");
}
