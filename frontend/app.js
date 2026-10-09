const videoInput = document.querySelector("#video-input");
const subtitleInput = document.querySelector("#subtitle-input");
const introInput = document.querySelector("#intro-input");
const youtubeUrlInput = document.querySelector("#youtube-url");
const rightsConfirmed = document.querySelector("#rights-confirmed");
const modelInput = document.querySelector("#model-input");
const createButton = document.querySelector("#create-button");
const cancelButton = document.querySelector("#cancel-button");
const form = document.querySelector("#job-form");
const progressCard = document.querySelector("#progress-card");
const statusPill = document.querySelector("#status-pill");
const stageLabel = document.querySelector("#stage-label");
const progressLabel = document.querySelector("#progress-label");
const progressBar = document.querySelector("#progress-bar");
const errorMessage = document.querySelector("#error-message");
const fileLinks = document.querySelector("#file-links");

let activeJobId = null;
let pollTimer = null;

const stageNames = {
  queued: "대기 중",
  parsing: "자막 읽는 중",
  translating: "한국어로 번역하는 중",
  formatting: "자막 파일 만드는 중",
  rendering: "영상에 자막 입히는 중",
  concatenating: "인트로 연결하는 중",
  completed: "완료",
  failed: "실패",
  cancelled: "취소됨",
};

function updateFileName(input) {
  const target = document.querySelector(`[data-file-name="${input.id}"]`);
  if (target) target.textContent = input.files?.[0]?.name || "선택된 파일 없음";
}

function canStart() {
  const hasVideo = Boolean(videoInput.files?.length);
  const hasUrl = Boolean(youtubeUrlInput.value.trim());
  return hasVideo || (hasUrl && rightsConfirmed.checked);
}

function updateCreateButton() {
  createButton.disabled = !canStart();
}

for (const input of [videoInput, subtitleInput, introInput]) {
  input.addEventListener("change", () => {
    updateFileName(input);
    updateCreateButton();
  });
}

youtubeUrlInput.addEventListener("input", updateCreateButton);
rightsConfirmed.addEventListener("change", updateCreateButton);

function setProgress(job) {
  const value = Math.max(0, Math.min(100, Number(job.progress) || 0));
  progressCard.hidden = false;
  statusPill.textContent = stageNames[job.status] || job.status;
  statusPill.dataset.status = job.status;
  stageLabel.textContent = stageNames[job.stage] || job.stage || "준비 중";
  progressLabel.textContent = `${Math.round(value)}%`;
  progressBar.style.width = `${value}%`;
  errorMessage.hidden = !job.error;
  errorMessage.textContent = job.error || "";
  cancelButton.hidden = !["queued", "parsing", "translating", "formatting", "rendering", "concatenating"].includes(job.status);
}

function clearLinks() {
  while (fileLinks.firstChild) fileLinks.removeChild(fileLinks.firstChild);
}

function renderLinks(job) {
  clearLinks();
  const files = Array.isArray(job.files) ? job.files : [];
  if (!files.length) return;
  const heading = document.createElement("p");
  heading.className = "links-heading";
  heading.textContent = "생성 파일";
  fileLinks.appendChild(heading);
  const allowed = new Set(["original.srt", "original.vtt", "korean.srt", "korean.ass", "qa.json", "subtitled.mp4", "final.mp4", "metadata.json", "transcript.json"]);
  for (const name of files) {
    if (!allowed.has(name)) continue;
    const link = document.createElement("a");
    link.className = "download-link";
    link.href = `/api/jobs/${encodeURIComponent(job.job_id)}/files/${encodeURIComponent(name)}`;
    link.download = name;
    link.textContent = `다운로드 · ${name}`;
    fileLinks.appendChild(link);
  }
}

async function loadModels() {
  try {
    const response = await fetch("/api/models");
    if (!response.ok) throw new Error("모델 목록을 불러오지 못했습니다.");
    const payload = await response.json();
    const names = Array.isArray(payload.models) ? payload.models : [];
    modelInput.replaceChildren();
    if (!names.length) {
      const option = new Option("Ollama 모델 없음", "");
      modelInput.add(option);
      updateCreateButton();
      return;
    }
    for (const name of names) modelInput.add(new Option(name, name));
    updateCreateButton();
  } catch (error) {
    modelInput.replaceChildren(new Option("모델 확인 실패 (기본값 사용)", ""));
    updateCreateButton();
  }
}

async function pollJob() {
  if (!activeJobId) return;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(activeJobId)}`);
    if (!response.ok) throw new Error("작업 상태를 읽지 못했습니다.");
    const job = await response.json();
    setProgress(job);
    renderLinks(job);
    if (["completed", "failed", "cancelled"].includes(job.status)) {
      activeJobId = null;
      updateCreateButton();
      return;
    }
    pollTimer = window.setTimeout(pollJob, 2000);
  } catch (error) {
    errorMessage.hidden = false;
    errorMessage.textContent = error.message;
    pollTimer = window.setTimeout(pollJob, 3000);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const hasVideo = Boolean(videoInput.files?.length);
  const hasUrl = Boolean(youtubeUrlInput.value.trim());
  if (!hasVideo && !hasUrl) {
    errorMessage.hidden = false;
    errorMessage.textContent = "영상 파일 또는 YouTube 링크를 선택하세요.";
    return;
  }
  if (hasVideo && hasUrl) {
    errorMessage.hidden = false;
    errorMessage.textContent = "파일과 링크 중 하나만 선택하세요.";
    return;
  }
  if (hasUrl && !rightsConfirmed.checked) {
    errorMessage.hidden = false;
    errorMessage.textContent = "YouTube 영상의 사용 권리를 확인하세요.";
    return;
  }
  if (pollTimer) window.clearTimeout(pollTimer);
  clearLinks();
  errorMessage.hidden = true;
  createButton.disabled = true;
  progressCard.hidden = false;
  stageLabel.textContent = "파일 준비 중";
  progressLabel.textContent = "0%";
  progressBar.style.width = "0%";
  const payload = new FormData();
  if (hasUrl) {
    payload.append("youtube_url", youtubeUrlInput.value.trim());
    payload.append("rights_confirmed", "true");
  } else {
    payload.append("video", videoInput.files[0]);
    if (subtitleInput.files?.length) payload.append("subtitle", subtitleInput.files[0]);
  }
  if (introInput.files?.length) payload.append("intro", introInput.files[0]);
  if (modelInput.value) payload.append("model", modelInput.value);
  try {
    const response = await fetch("/api/jobs", { method: "POST", body: payload });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "작업을 만들지 못했습니다.");
    activeJobId = body.job_id;
    await pollJob();
  } catch (error) {
    errorMessage.hidden = false;
    errorMessage.textContent = error.message;
    updateCreateButton();
  }
});

cancelButton.addEventListener("click", async () => {
  if (!activeJobId) return;
  cancelButton.disabled = true;
  try {
    await fetch(`/api/jobs/${encodeURIComponent(activeJobId)}/cancel`, { method: "POST" });
    await pollJob();
  } finally {
    cancelButton.disabled = false;
  }
});

loadModels();
