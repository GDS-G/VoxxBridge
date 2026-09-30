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
type AppAudioMetadata = Pick<AudioContent, "data" | "mimeType">;
type PlaybackState =
  | "unavailable"
  | "resource"
  | "ready"
  | "decoding"
  | "playing"
  | "paused"
  | "ended";

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
  sha256?: string;
};

type OpenAIFileBridge = {
  uploadFile?: (
    file: File,
    options?: { library?: boolean },
  ) => Promise<string | { fileId?: string; file_id?: string }>;
  selectFiles?: () => Promise<unknown[]>;
  getFileDownloadUrl?: (
    input: { fileId: string },
  ) => Promise<{ downloadUrl?: string; download_url?: string }>;
};

type FileHandoffStage =
  | "resource"
  | "integrity"
  | "size"
  | "file"
  | "upload"
  | "response";

const MAX_CHATGPT_UPLOAD_BYTES = 8 * 1024 * 1024;
const BASE64_PATTERN = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

const app = new App(
  { name: "VoxBridge Audio Delivery", version: "0.3.0" },
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
const saveChatGptButton = requiredElement<HTMLButtonElement>("save-chatgpt");
const saveChatGptLabel = saveChatGptButton.querySelector<HTMLElement>(".button-label");
const status = requiredElement<HTMLParagraphElement>("status");

if (!buttonLabel || !saveChatGptLabel) {
  throw new Error("Missing file action button label");
}

const resolvedButtonLabel = buttonLabel;
const resolvedSaveChatGptLabel = saveChatGptLabel;

let currentResource: ResourceLink | undefined;
let isConnected = false;
let hostCanDownload = false;
let hostCanReadResources = false;
let hostCanUploadFile = false;
let hostHasFileLibrary = false;
let isDownloading = false;
let isSavingToChatGpt = false;
let savedChatGptFileId: string | undefined;
let cachedFileResourceUri: string | undefined;
let cachedFileBase64: string | undefined;
let cachedFileBytes: ArrayBuffer | undefined;
let cachedFileMimeType: string | undefined;
let currentMetadata: SpeechMetadata = {};
let latestResult: ToolResult | undefined;
let resultRevision = 0;
let playbackRevision = 0;
let playbackResource: ResourceLink | undefined;
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

function getOpenAIFileBridge(): OpenAIFileBridge | undefined {
  return (window as typeof window & { openai?: OpenAIFileBridge }).openai;
}

function fileHandoffCode(stage: FileHandoffStage) {
  const codes: Record<FileHandoffStage, string> = {
    resource: "VB-HANDOFF-READ",
    integrity: "VB-HANDOFF-INTEGRITY",
    size: "VB-HANDOFF-SIZE",
    file: "VB-HANDOFF-FILE",
    upload: "VB-HANDOFF-UPLOAD",
    response: "VB-HANDOFF-RESPONSE",
  };
  return codes[stage];
}

function recordFileHandoffFailure(stage: FileHandoffStage, error: unknown) {
  // Host exceptions can contain temporary URLs or opaque identifiers. Keep the
  // user-visible and console diagnostics stable without echoing those values.
  const errorType = error instanceof DOMException
    ? error.name
    : error instanceof Error
      ? error.constructor.name
      : typeof error;
  console.warn(`[VoxBridge] ${fileHandoffCode(stage)} (${errorType})`);
}

function extractUploadedFileId(result: unknown) {
  if (typeof result === "string") return result.trim() || undefined;
  if (!result || typeof result !== "object" || Array.isArray(result)) return undefined;
  const candidate = result as { fileId?: unknown; file_id?: unknown };
  const fileId = candidate.fileId ?? candidate.file_id;
  return typeof fileId === "string" && fileId.trim() ? fileId : undefined;
}

function extractDownloadUrl(result: unknown) {
  if (!result || typeof result !== "object" || Array.isArray(result)) return undefined;
  const candidate = result as { downloadUrl?: unknown; download_url?: unknown };
  const downloadUrl = candidate.downloadUrl ?? candidate.download_url;
  return typeof downloadUrl === "string" && downloadUrl.trim() ? downloadUrl : undefined;
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

function setSavingToChatGpt(value: boolean) {
  isSavingToChatGpt = value;
  saveChatGptButton.disabled = value || !hostCanUploadFile || Boolean(savedChatGptFileId);
  saveChatGptButton.setAttribute("aria-busy", String(value));
  resolvedSaveChatGptLabel.textContent = value
    ? "Saving to ChatGPT…"
    : savedChatGptFileId
      ? "Saved to ChatGPT"
      : hostHasFileLibrary
        ? "Save to ChatGPT"
        : "Upload to ChatGPT";
}

function resetFileHandoff() {
  savedChatGptFileId = undefined;
  cachedFileResourceUri = undefined;
  cachedFileBase64 = undefined;
  cachedFileBytes = undefined;
  cachedFileMimeType = undefined;
  setSavingToChatGpt(false);
}

function updateFileActions(resource: ResourceLink | undefined) {
  const canSaveToChatGpt =
    hostCanUploadFile &&
    (resource?.size === undefined || resource.size <= MAX_CHATGPT_UPLOAD_BYTES);
  downloadButton.hidden = !hostCanDownload;
  saveChatGptButton.hidden = !canSaveToChatGpt;
  actions.hidden = !resource || (!hostCanDownload && !canSaveToChatGpt);
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
  if (
    !mimeType.toLowerCase().startsWith("audio/") ||
    data.length === 0 ||
    !BASE64_PATTERN.test(data)
  ) {
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

function base64DecodedByteLength(data: string) {
  if (data.length === 0 || !BASE64_PATTERN.test(data)) {
    throw new Error("Invalid base64 audio content");
  }
  const padding = data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0;
  return (data.length / 4) * 3 - padding;
}

async function verifyFileBytes(
  bytes: ArrayBuffer,
  resource: ResourceLink,
  metadata: SpeechMetadata,
) {
  const expectedSize = resource.size ?? metadata.file_size_bytes;
  if (expectedSize !== undefined && bytes.byteLength !== expectedSize) {
    throw new Error("Generated audio size does not match its file metadata");
  }
  const expectedDigest = metadata.sha256?.toLowerCase();
  if (!expectedDigest) return;
  if (!/^[a-f0-9]{64}$/.test(expectedDigest)) {
    throw new Error("Generated audio has invalid integrity metadata");
  }
  if (!globalThis.crypto?.subtle) return;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes.slice(0));
  const actualDigest = Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  if (actualDigest !== expectedDigest) {
    throw new Error("Generated audio failed its integrity check");
  }
}

function parseAppAudioMetadata(result: ToolResult): AppAudioMetadata | undefined {
  const value = result._meta?.["voxbridge/audio"];
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;

  const data = "data" in value ? value.data : undefined;
  const mimeType = "mimeType" in value ? value.mimeType : undefined;
  if (typeof data !== "string" || typeof mimeType !== "string") return undefined;
  return { data, mimeType };
}

function parseResourceAudio(result: ResourceReadResult, resource: ResourceLink) {
  for (const content of result.contents) {
    if (!isBlobResourceContents(content)) continue;
    const mimeType = content.mimeType ?? resource.mimeType;
    if (mimeType?.toLowerCase().startsWith("audio/")) {
      return { data: content.blob, mimeType };
    }
  }
  throw new Error("Expected a base64 audio blob resource");
}

async function loadFileResource(resource: ResourceLink) {
  if (
    cachedFileResourceUri === resource.uri &&
    cachedFileBase64 &&
    cachedFileMimeType
  ) {
    return { data: cachedFileBase64, mimeType: cachedFileMimeType };
  }
  if (!hostCanReadResources) {
    throw new Error("This host cannot read the generated audio resource");
  }
  const result = await app.readServerResource({ uri: resource.uri });
  const audio = parseResourceAudio(result, resource);
  cachedFileResourceUri = resource.uri;
  cachedFileBase64 = audio.data;
  cachedFileMimeType = audio.mimeType;
  return audio;
}

async function loadFileBytes(resource: ResourceLink) {
  if (
    cachedFileResourceUri === resource.uri &&
    cachedFileBytes &&
    cachedFileMimeType
  ) {
    return { bytes: cachedFileBytes, mimeType: cachedFileMimeType };
  }
  const audio = await loadFileResource(resource);
  const bytes = decodeBase64Audio(audio.data, audio.mimeType);
  cachedFileResourceUri = resource.uri;
  cachedFileBytes = bytes;
  cachedFileMimeType = audio.mimeType;
  return { bytes, mimeType: audio.mimeType };
}

function verifyFileEnvelope(
  data: string,
  resource: ResourceLink,
  metadata: SpeechMetadata,
) {
  const expectedSize = resource.size ?? metadata.file_size_bytes;
  if (expectedSize !== undefined && base64DecodedByteLength(data) !== expectedSize) {
    throw new Error("Generated audio size does not match its file metadata");
  }
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
  playbackResource = undefined;
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

function prepareResourcePlayback(resource: ResourceLink) {
  clearPlayback();
  playbackResource = resource;
  playbackState = "resource";
  playback.hidden = false;
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

function renderResult(result: ToolResult) {
  latestResult = result;
  resultRevision += 1;
  clearPlayback();
  resetFileHandoff();
  if (result.isError) {
    currentResource = undefined;
    currentMetadata = {};
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
  currentMetadata = metadata;
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

  updateFileActions(resource);
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
    const appAudio = parseAppAudioMetadata(result);
    if (appAudio) {
      cachedFileResourceUri = resource.uri;
      cachedFileBase64 = appAudio.data;
      cachedFileMimeType = appAudio.mimeType;
    }
    if (appAudio && preparePlayback(appAudio)) {
      cachedFileBytes = playbackBytes;
      setStatus(
        hostCanDownload
          ? "Ready to play or download."
          : "Ready to play. Use the host's attached-file Download action below.",
      );
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
    prepareResourcePlayback(resource);
    setStatus(
      hostCanDownload
        ? "Ready to download. Choose Play audio to load the file for playback."
        : "Choose Play audio to load the file for playback. The host's attached-file Download action remains available below.",
    );
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

  if (playbackState === "decoding" || (!playbackBytes && !playbackResource)) return;

  const revision = playbackRevision;
  const resource = playbackResource;
  let bytes = playbackBytes;
  const previousState = playbackState;
  if (previousState === "ended") {
    playbackOffsetSeconds = 0;
  }
  playbackState = "decoding";
  playPauseButton.disabled = true;
  playPauseButton.setAttribute("aria-busy", "true");
  playbackLabel.textContent = bytes ? "Preparing audio…" : "Loading audio…";
  setStatus(bytes ? "Preparing playback…" : "Loading audio for playback…");

  let context = playbackContext;
  try {
    if (!context || context.state === "closed") {
      context = new AudioContext();
      playbackContext = context;
    }

    const resumePromise = context.state === "suspended" ? context.resume() : Promise.resolve();
    if (!bytes) {
      if (!resource) return;
      const [result] = await Promise.all([
        app.readServerResource({ uri: resource.uri }),
        resumePromise,
      ]);
      if (
        revision !== playbackRevision ||
        playbackResource?.uri !== resource.uri ||
        playbackContext !== context
      ) {
        return;
      }
      const resourceAudio = parseResourceAudio(result, resource);
      bytes = decodeBase64Audio(resourceAudio.data, resourceAudio.mimeType);
      playbackBytes = bytes;
    } else {
      await resumePromise;
    }

    const decodePromise: Promise<AudioBuffer> = playbackBuffer
      ? Promise.resolve(playbackBuffer)
      : context.decodeAudioData(bytes.slice(0));
    const decodedBuffer = await decodePromise;

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
    if (revision !== playbackRevision) return;
    stopPlaybackSource();
    if (playbackContext === context) {
      playbackContext = undefined;
    }
    closePlaybackContext(context);
    if (!playbackBytes && playbackResource) {
      playbackState = "resource";
      playPauseButton.disabled = false;
      playPauseButton.setAttribute("aria-busy", "false");
      playPauseButton.setAttribute("aria-pressed", "false");
      playbackIcon.textContent = "▶";
      playbackLabel.textContent = "Play audio";
      setStatus(
        `Playback could not load yet. If file approval is pending, allow it and choose Play audio again. ${fileDeliveryStatus()}`,
        "error",
      );
      return;
    }
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
    let contents: Parameters<App["downloadFile"]>[0]["contents"] = [resource];
    let loadedFile: Awaited<ReturnType<typeof loadFileResource>> | undefined;
    try {
      loadedFile = await loadFileResource(resource);
    } catch {
      // A ResourceLink remains a portable fallback when inline materialization
      // is unavailable; the host fetches it through the originating MCP server.
    }
    if (loadedFile) {
      const { data, mimeType } = loadedFile;
      verifyFileEnvelope(data, resource, currentMetadata);
      contents = [
        {
          type: "resource",
          resource: {
            uri: `file:///${encodeURIComponent(resource.name)}`,
            mimeType,
            blob: data,
          },
        },
      ];
    }
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    const result = await app.downloadFile({ contents });
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

async function saveCurrentFileToChatGpt() {
  if (isSavingToChatGpt || savedChatGptFileId || !currentResource) return;
  const bridge = getOpenAIFileBridge();
  if (!bridge?.uploadFile) {
    setStatus("This ChatGPT host does not expose its file library.", "error");
    return;
  }

  const resource = currentResource;
  const revision = resultRevision;
  let stage: FileHandoffStage = "resource";
  setSavingToChatGpt(true);
  setStatus(
    hostHasFileLibrary
      ? "Saving the generated audio to your ChatGPT file library…"
      : "Uploading the generated audio to ChatGPT…",
  );
  try {
    const { bytes, mimeType } = await loadFileBytes(resource);
    stage = "integrity";
    await verifyFileBytes(bytes, resource, currentMetadata);
    stage = "size";
    if (bytes.byteLength > MAX_CHATGPT_UPLOAD_BYTES) {
      throw new Error("Audio is too large for ChatGPT file-library upload");
    }
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    stage = "file";
    const file = new File([bytes], resource.name, { type: mimeType });
    stage = "upload";
    // The library option is only valid when the optional file-library helper is
    // present. Hosts that expose uploadFile without selectFiles can still accept
    // the file for this plugin/session, so do not turn an optional library into
    // a hard failure.
    const uploaded = hostHasFileLibrary
      ? await bridge.uploadFile(file, { library: true })
      : await bridge.uploadFile(file);
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    stage = "response";
    const uploadedFileId = extractUploadedFileId(uploaded);
    if (!uploadedFileId) {
      throw new Error("ChatGPT did not return a file id");
    }
    let confirmedAvailable = false;
    if (bridge.getFileDownloadUrl) {
      try {
        const downloadable = await bridge.getFileDownloadUrl({ fileId: uploadedFileId });
        confirmedAvailable = Boolean(extractDownloadUrl(downloadable));
      } catch {
        // The upload itself succeeded. URL confirmation is an optional second
        // check and must not misreport a saved file as an upload failure.
      }
    }
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    savedChatGptFileId = uploadedFileId;
    setStatus(
      hostHasFileLibrary
        ? confirmedAvailable
          ? "Saved to ChatGPT and confirmed available. Select this audio from your file library for a later tool or message."
          : "Saved to ChatGPT. Select this audio from your file library for a later tool or message."
        : confirmedAvailable
          ? "Uploaded to ChatGPT and confirmed available to this plugin. This host does not expose the reusable file library."
          : "Uploaded to ChatGPT for this plugin. This host does not expose the reusable file library.",
      "success",
    );
  } catch (error) {
    if (revision !== resultRevision || currentResource?.uri !== resource.uri) return;
    recordFileHandoffFailure(stage, error);
    setStatus(
      `ChatGPT file handoff failed (${fileHandoffCode(stage)}). Download is still available.`,
      "error",
    );
  } finally {
    if (revision === resultRevision && currentResource?.uri === resource.uri) {
      setSavingToChatGpt(false);
    }
  }
}

downloadButton.addEventListener("click", () => {
  void downloadCurrentResource();
});

saveChatGptButton.addEventListener("click", () => {
  void saveCurrentFileToChatGpt();
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
  currentMetadata = {};
  resetFileHandoff();
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
  currentMetadata = {};
  resetFileHandoff();
  clearPlayback();
  setDownloading(false);
  actions.hidden = true;
  summary.textContent = "Audio generation was cancelled.";
  setStatus(params.reason ? `Cancelled: ${params.reason}` : "Cancelled.");
});

app.onteardown = () => {
  resultRevision += 1;
  currentResource = undefined;
  currentMetadata = {};
  resetFileHandoff();
  clearPlayback();
  return {};
};

window.addEventListener("pagehide", () => {
  resultRevision += 1;
  currentResource = undefined;
  currentMetadata = {};
  resetFileHandoff();
  clearPlayback();
});

try {
  await app.connect();
  isConnected = true;
  const hostCapabilities = app.getHostCapabilities();
  hostCanDownload = hostCapabilities?.downloadFile !== undefined;
  hostCanReadResources = hostCapabilities?.serverResources !== undefined;
  const openaiFileBridge = getOpenAIFileBridge();
  hostCanUploadFile = typeof openaiFileBridge?.uploadFile === "function";
  hostHasFileLibrary = typeof openaiFileBridge?.selectFiles === "function";
  setSavingToChatGpt(false);
  downloadButton.disabled = !hostCanDownload;
  saveChatGptButton.disabled = !hostCanUploadFile;
  downloadButton.hidden = !hostCanDownload;
  saveChatGptButton.hidden = !hostCanUploadFile;
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
