const tabs = document.querySelectorAll(".nav-item");
const sections = document.querySelectorAll(".page-section");
const pageTitle = document.getElementById("page-title");
const dashboardPayload = JSON.parse(document.getElementById("dashboard-data").textContent);
const reviewPayloadElement = document.getElementById("review-data");
const reviewPayload = reviewPayloadElement ? JSON.parse(reviewPayloadElement.textContent) : { incidents: [] };
const appShell = document.querySelector(".app-shell");
const teacherReviewCard = document.getElementById("teacher-review-card");
const teacherReviewStatus = document.getElementById("teacher-review-status");
const teacherReviewFeedback = document.getElementById("teacher-review-feedback");
const confirmFraudButton = document.getElementById("confirm-fraud-button");
const dismissReviewButton = document.getElementById("dismiss-review-button");

const titleMap = {
  overview: "System Oversight",
  review: "Chi tiết Hậu Kiểm",
  students: "Danh sách Thí sinh & Vi phạm",
  settings: "Cấu hình Hệ thống AI",
};

function activateTab(target) {
  const resolvedTab = titleMap[target] ? target : "overview";
  tabs.forEach((item) => item.classList.toggle("is-active", item.dataset.tab === resolvedTab));
  sections.forEach((section) => section.classList.toggle("is-active", section.id === resolvedTab));
  pageTitle.textContent = titleMap[resolvedTab];
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    activateTab(tab.dataset.tab);
  });
});

const riskLabels = {
  high: "Rất cao",
  medium: "Trung bình",
  low: "An toàn",
};

const teacherVerdictLabels = {
  confirmed: "Gian lận",
  dismissed: "Không gian lận",
  pending: "Chưa kết luận",
};

const studentTableBody = document.getElementById("student-table-body");
const studentTableSummary = document.getElementById("student-table-summary");
const riskChips = document.querySelectorAll(".filter-chip");
const exportStudentsCsvButton = document.getElementById("export-students-csv");
const globalSearchInput = document.getElementById("global-search-input");
const globalSearchSuggestions = document.getElementById("global-search-suggestions");
const allStudents = Array.isArray(dashboardPayload.students) ? dashboardPayload.students : [];
let activeRiskFilter = "all";
let activeSearchQuery = "";

function normalizeTeacherReview(student) {
  const review = student && typeof student === "object" ? (student.teacher_review || {}) : {};
  const status = ["confirmed", "dismissed"].includes(review.status) ? review.status : "pending";
  return {
    status,
    label: teacherVerdictLabels[status],
    decided_at: review.decided_at || null,
  };
}

function getReviewedCandidateIds() {
  const candidateIds = new Set();
  if (Array.isArray(reviewPayload.students_report)) {
    reviewPayload.students_report.forEach((student) => {
      const candidateId = String(student?.candidate_id || "").trim();
      if (candidateId && candidateId !== "UNKNOWN") {
        candidateIds.add(candidateId);
      }
    });
  }
  if (!candidateIds.size) {
    const primaryCandidateId = String(reviewPayload.primary_candidate?.candidate_id || "").trim();
    if (primaryCandidateId && primaryCandidateId !== "UNKNOWN") {
      candidateIds.add(primaryCandidateId);
    }
  }
  return candidateIds;
}

function syncStudentTeacherReview() {
  const reviewedCandidateIds = getReviewedCandidateIds();
  allStudents.forEach((student) => {
    const candidateId = String(student?.candidate_id || "").trim();
    if (!reviewedCandidateIds.has(candidateId)) {
      return;
    }
    student.teacher_review = normalizeTeacherReview({ teacher_review: reviewPayload.teacher_review || {} });
  });
}

function renderTeacherReview() {
  const review = reviewPayload.teacher_review || {};
  const status = review.status || "pending";
  if (teacherReviewCard) {
    teacherReviewCard.classList.remove("risk-card-pending", "risk-card-confirmed", "risk-card-dismissed");
    teacherReviewCard.classList.add(`risk-card-${status}`);
    if (status === "dismissed") {
      teacherReviewCard.style.background = "linear-gradient(180deg, rgba(232, 236, 242, 0.96), rgba(217, 223, 231, 0.88))";
    } else if (status === "confirmed") {
      teacherReviewCard.style.background = "linear-gradient(180deg, rgba(255, 218, 214, 0.98), rgba(255, 191, 184, 0.9))";
    } else {
      teacherReviewCard.style.background = "linear-gradient(180deg, rgba(255, 218, 214, 0.9), rgba(255, 218, 214, 0.7))";
    }
  }
  if (teacherReviewStatus) {
    teacherReviewStatus.textContent = `Quyet dinh giao vien: ${review.label || "Chua quyet dinh"}`;
  }
}

function showTeacherReviewFeedback(message, isError = false) {
  if (!teacherReviewFeedback) {
    return;
  }
  teacherReviewFeedback.hidden = false;
  teacherReviewFeedback.textContent = message;
  teacherReviewFeedback.style.color = isError ? "#b42318" : "#0f5132";
}

async function submitTeacherReviewDecision(decision) {
  if (!reviewPayload.result_path && !reviewPayload.video_path) {
    showTeacherReviewFeedback("Khong tim thay lan hau kiem de cap nhat.", true);
    return;
  }

  if (confirmFraudButton) {
    confirmFraudButton.disabled = true;
  }
  if (dismissReviewButton) {
    dismissReviewButton.disabled = true;
  }
  showTeacherReviewFeedback("Dang cap nhat quyet dinh...");

  try {
    const response = await fetch("/review/decision", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        decision,
        result_path: reviewPayload.result_path || null,
        video_path: reviewPayload.video_path || null,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload?.message || "Khong the cap nhat quyet dinh.");
    }
    reviewPayload.teacher_review = payload.teacher_review || {};
    renderTeacherReview();
    syncStudentTeacherReview();
    renderStudents();
    showTeacherReviewFeedback(payload.message || "Da cap nhat quyet dinh.");
  } catch (error) {
    showTeacherReviewFeedback(error.message || "Khong the cap nhat quyet dinh.", true);
  } finally {
    if (confirmFraudButton) {
      confirmFraudButton.disabled = false;
    }
    if (dismissReviewButton) {
      dismissReviewButton.disabled = false;
    }
  }
}

function normalizeText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

function tokenize(value) {
  return normalizeText(value).split(/[^a-z0-9]+/).filter(Boolean);
}

function buildStudentTerms(student) {
  const terms = [
    student?.name,
    student?.candidate_id,
    student?.room,
    student?.email,
  ];
  return terms.map((value) => String(value || "").trim()).filter(Boolean);
}

function buildStudentTokens(student) {
  const behaviorText = Array.isArray(student?.behaviors) ? student.behaviors.join(" ") : "";
  const combined = [
    student?.name,
    student?.candidate_id,
    student?.room,
    student?.email,
    behaviorText,
  ].join(" ");
  return tokenize(combined);
}

const studentSearchIndex = allStudents.map((student) => ({
  student,
  terms: buildStudentTerms(student),
  tokens: buildStudentTokens(student),
}));

function searchMatchesEntry(entry, query) {
  const queryTokens = tokenize(query);
  if (!queryTokens.length) {
    return true;
  }
  return queryTokens.every((queryToken) => entry.tokens.some((token) => token.startsWith(queryToken) || token.includes(queryToken)));
}

function termMatchesQuery(term, query) {
  const queryTokens = tokenize(query);
  if (!queryTokens.length) {
    return false;
  }
  const termTokens = tokenize(term);
  return queryTokens.every((queryToken) => termTokens.some((token) => token.startsWith(queryToken)));
}

function updateSearchSuggestions(query) {
  if (!globalSearchSuggestions) {
    return;
  }

  globalSearchSuggestions.innerHTML = "";
  if (!query.trim()) {
    return;
  }

  const seen = new Set();
  const suggestions = [];

  studentSearchIndex.forEach((entry) => {
    entry.terms.forEach((term) => {
      const key = normalizeText(term);
      if (!key || seen.has(key)) {
        return;
      }
      if (!termMatchesQuery(term, query)) {
        return;
      }
      seen.add(key);
      suggestions.push(term);
    });
  });

  suggestions.slice(0, 10).forEach((term) => {
    const option = document.createElement("option");
    option.value = term;
    globalSearchSuggestions.appendChild(option);
  });
}

function getFilteredStudents() {
  return studentSearchIndex
    .filter((entry) => (activeRiskFilter === "all" || entry.student.risk === activeRiskFilter) && searchMatchesEntry(entry, activeSearchQuery))
    .map((entry) => entry.student);
}

function behaviorChips(behaviors) {
  if (!Array.isArray(behaviors) || !behaviors.length) {
    return '<span class="table-chip">Không có</span>';
  }
  return behaviors.map((behavior) => `<span class="table-chip">${behavior}</span>`).join("");
}

function renderStudents() {
  if (!studentTableBody || !studentTableSummary) {
    return;
  }

  const rows = getFilteredStudents();
  if (!rows.length) {
    studentTableBody.innerHTML = `
      <tr>
        <td colspan="6">
          <div class="empty-state">Không có kết quả phù hợp với bộ lọc hiện tại.</div>
        </td>
      </tr>
    `;
    studentTableSummary.textContent = `Hiển thị 0 mục trong số ${allStudents.length} hồ sơ tiêu biểu`;
    return;
  }

  studentTableBody.innerHTML = rows.map((student) => {
    const teacherReview = normalizeTeacherReview(student);
    return `
    <tr>
      <td>
        <div class="candidate-cell">
          <span class="table-avatar">${String(student.name || "NA").trim().split(/\s+/).slice(-1)[0].slice(0, 2).toUpperCase()}</span>
          <div>
            <strong>${student.name}</strong>
            <small>${student.email}</small>
          </div>
        </div>
      </td>
      <td>
        <div class="cell-stack">
          <strong>${student.candidate_id}</strong>
          <small>${student.room}</small>
        </div>
      </td>
      <td><div class="table-chip-row">${behaviorChips(student.behaviors)}</div></td>
      <td><strong>${student.alerts}</strong></td>
      <td><span class="risk-badge risk-${student.risk}">${riskLabels[student.risk]}</span></td>
      <td><span class="verdict-badge verdict-${teacherReview.status}">${teacherReview.label}</span></td>
    </tr>
  `;
  }).join("");

  studentTableSummary.textContent = `Hiển thị ${rows.length} mục trong số ${allStudents.length} hồ sơ tiêu biểu`;
}

function downloadStudentsReport() {
  const students = Array.isArray(dashboardPayload.students) ? dashboardPayload.students : [];
  if (!students.length) {
    alert("Chua co du lieu thi sinh de xuat bao cao.");
    return;
  }
  window.location.href = "/students/export.xls";
}

riskChips.forEach((chip) => {
  chip.addEventListener("click", () => {
    riskChips.forEach((item) => item.classList.remove("is-selected"));
    chip.classList.add("is-selected");
    activeRiskFilter = chip.dataset.risk || "all";
    renderStudents();
  });
});

if (globalSearchInput) {
  const applyGlobalSearch = (openStudentsTab = false) => {
    activeSearchQuery = globalSearchInput.value || "";
    updateSearchSuggestions(activeSearchQuery);
    if (openStudentsTab) {
      activateTab("students");
    }
    renderStudents();
  };

  globalSearchInput.addEventListener("input", () => {
    applyGlobalSearch(false);
  });

  globalSearchInput.addEventListener("change", () => {
    applyGlobalSearch(false);
  });

  globalSearchInput.addEventListener("search", () => {
    applyGlobalSearch(false);
  });

  globalSearchInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    applyGlobalSearch(true);
  });
}

if (exportStudentsCsvButton) {
  exportStudentsCsvButton.addEventListener("click", downloadStudentsReport);
}

const confidenceRange = document.getElementById("confidence-range");
const confidenceValue = document.getElementById("confidence-value");
const intervalRange = document.getElementById("interval-range");
const intervalValue = document.getElementById("interval-value");
const behaviorThresholdRange = document.getElementById("behavior-threshold-range");
const behaviorThresholdValue = document.getElementById("behavior-threshold-value");
const toggleGazeAlerts = document.getElementById("toggle-gaze-alerts");
const toggleCellPhoneAlerts = document.getElementById("toggle-cell-phone-alerts");
const toggleMultiplePeopleAlerts = document.getElementById("toggle-multiple-people-alerts");
const saveSettingsButton = document.getElementById("save-settings-button");
const resetSettingsButton = document.getElementById("reset-settings-button");
const settingsFeedback = document.getElementById("settings-feedback");

if (confidenceRange && confidenceValue) {
  confidenceRange.addEventListener("input", () => {
    confidenceValue.textContent = Number(confidenceRange.value).toFixed(2);
  });
}

if (intervalRange && intervalValue) {
  intervalValue.textContent = Number(intervalRange.value).toFixed(2);
  intervalRange.addEventListener("input", () => {
    intervalValue.textContent = Number(intervalRange.value).toFixed(2);
  });
}

if (behaviorThresholdRange && behaviorThresholdValue) {
  behaviorThresholdRange.addEventListener("input", () => {
    behaviorThresholdValue.textContent = Number(behaviorThresholdRange.value).toFixed(2);
  });
}

function showSettingsFeedback(message, isError = false) {
  if (!settingsFeedback) {
    return;
  }
  settingsFeedback.hidden = false;
  settingsFeedback.textContent = message;
  settingsFeedback.style.color = isError ? "#b42318" : "#0f5132";
}

function collectAiSettings() {
  return {
    confidence_threshold: Number(confidenceRange?.value || 0.75),
    extraction_interval_seconds: Number(intervalRange?.value || 2.0),
    behavior_threshold: Number(behaviorThresholdRange?.value || 0.82),
    enable_gaze_alerts: Boolean(toggleGazeAlerts?.checked),
    enable_cell_phone_alerts: Boolean(toggleCellPhoneAlerts?.checked),
    enable_multiple_people_alerts: Boolean(toggleMultiplePeopleAlerts?.checked),
  };
}

function applyAiSettings(settings) {
  if (confidenceRange && confidenceValue && typeof settings.confidence_threshold === "number") {
    confidenceRange.value = settings.confidence_threshold.toFixed(2);
    confidenceValue.textContent = settings.confidence_threshold.toFixed(2);
  }
  if (intervalRange && intervalValue && typeof settings.extraction_interval_seconds === "number") {
    intervalRange.value = settings.extraction_interval_seconds.toFixed(2);
    intervalValue.textContent = settings.extraction_interval_seconds.toFixed(2);
  }
  if (behaviorThresholdRange && behaviorThresholdValue && typeof settings.behavior_threshold === "number") {
    behaviorThresholdRange.value = settings.behavior_threshold.toFixed(2);
    behaviorThresholdValue.textContent = settings.behavior_threshold.toFixed(2);
  }
  if (toggleGazeAlerts && typeof settings.enable_gaze_alerts === "boolean") {
    toggleGazeAlerts.checked = settings.enable_gaze_alerts;
  }
  if (toggleCellPhoneAlerts && typeof settings.enable_cell_phone_alerts === "boolean") {
    toggleCellPhoneAlerts.checked = settings.enable_cell_phone_alerts;
  }
  if (toggleMultiplePeopleAlerts && typeof settings.enable_multiple_people_alerts === "boolean") {
    toggleMultiplePeopleAlerts.checked = settings.enable_multiple_people_alerts;
  }
}

async function saveAiSettings() {
  if (!saveSettingsButton) {
    return;
  }

  saveSettingsButton.disabled = true;
  showSettingsFeedback("Dang luu cau hinh...");

  try {
    const response = await fetch("/settings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(collectAiSettings()),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload?.message || "Khong the luu cau hinh.");
    }
    applyAiSettings(payload.settings || {});
    showSettingsFeedback(payload.message || "Da luu cau hinh.");
  } catch (error) {
    showSettingsFeedback(error.message || "Khong the luu cau hinh.", true);
  } finally {
    saveSettingsButton.disabled = false;
  }
}

async function resetAiSettings() {
  if (!resetSettingsButton) {
    return;
  }

  resetSettingsButton.disabled = true;
  showSettingsFeedback("Dang khoi phuc cau hinh mac dinh...");

  try {
    const response = await fetch("/settings/reset", {
      method: "POST",
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload?.message || "Khong the khoi phuc cau hinh.");
    }
    applyAiSettings(payload.settings || payload.defaults || {});
    showSettingsFeedback(payload.message || "Da khoi phuc cau hinh mac dinh.");
  } catch (error) {
    showSettingsFeedback(error.message || "Khong the khoi phuc cau hinh.", true);
  } finally {
    resetSettingsButton.disabled = false;
  }
}

if (saveSettingsButton) {
  saveSettingsButton.addEventListener("click", saveAiSettings);
}

if (resetSettingsButton) {
  resetSettingsButton.addEventListener("click", resetAiSettings);
}

const uploadTriggers = document.querySelectorAll(".upload-trigger");
const uploadFileInput = document.getElementById("review-video-file");
const selectedFileName = document.getElementById("selected-file-name");
const clearSelectedFileButton = document.getElementById("clear-selected-file");
const uploadSubmitButton = document.getElementById("upload-submit-button");

uploadTriggers.forEach((trigger) => {
  trigger.addEventListener("click", () => {
    const fileInputId = trigger.dataset.fileTrigger;
    const input = document.getElementById(fileInputId);
    if (input) {
      input.click();
    }
  });
});

function syncSelectedFileState() {
  if (!uploadFileInput || !selectedFileName) {
    return;
  }

  const [file] = uploadFileInput.files;
  const hasFile = Boolean(file);

  selectedFileName.textContent = hasFile ? `Đã chọn: ${file.name}` : "Chưa chọn tệp nào";

  if (clearSelectedFileButton) {
    clearSelectedFileButton.hidden = !hasFile;
  }

  if (uploadSubmitButton) {
    uploadSubmitButton.disabled = !hasFile;
  }
}

if (uploadFileInput && selectedFileName) {
  uploadFileInput.addEventListener("change", syncSelectedFileState);
}

if (clearSelectedFileButton && uploadFileInput) {
  clearSelectedFileButton.addEventListener("click", () => {
    uploadFileInput.value = "";
    syncSelectedFileState();
    uploadFileInput.click();
  });
}

const reviewVideoPlayer = document.getElementById("review-video-player");
const liveCameraPlayer = document.getElementById("live-camera-player");
const reviewVideoStage = document.getElementById("review-video-stage");
const stagePlaceholder = document.getElementById("review-stage-placeholder");
const toggleLiveTestButton = document.getElementById("toggle-live-test-button");
const liveTestFeedback = document.getElementById("live-test-feedback");
const stageFlagText = document.getElementById("review-stage-flag-text");
const stageFlag = document.querySelector(".stage-flag");
const incidentList = document.getElementById("incident-list");
const initialIncidentMarkup = incidentList ? incidentList.innerHTML : "";
let incidentCards = Array.from(document.querySelectorAll(".incident-card[data-incident-time]"));
const incidentCountChip = document.getElementById("incident-count-chip");
const defaultStageFlagText = "Binh thuong";
const emptyStageFlagText = "Chua co video de hau kiem";
const genericIncidentLabel = "Hanh vi nghi ngo gian lan";
const liveCameraRequestText = "Dang xin quyen camera...";
const liveCameraDeniedText = "Khong the bat camera de live test";
const liveWaitingIncidentText = "Dang cho canh bao tu webcam.";
const incidentLookbackSeconds = 0.35;
const incidentDisplayWindowSeconds = 2.4;
const liveFrameIntervalMs = 450;
const liveFrameMaxWidth = 720;
const liveCaptureCanvas = document.createElement("canvas");
const liveCaptureContext = liveCaptureCanvas.getContext("2d");
let liveModeStarted = false;
let liveModeStarting = false;
let liveStream = null;
let livePollTimer = null;
let livePollInFlight = false;

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;");
}

function refreshIncidentCards() {
  incidentCards = Array.from(document.querySelectorAll(".incident-card[data-incident-time]"));
}

function setLiveTestFeedback(message, isError = false) {
  if (!liveTestFeedback) {
    return;
  }
  liveTestFeedback.textContent = message;
  liveTestFeedback.style.color = isError ? "#b42318" : "";
}

function updateLiveToggleButton() {
  if (!toggleLiveTestButton) {
    return;
  }
  if (liveModeStarting) {
    toggleLiveTestButton.disabled = true;
    toggleLiveTestButton.textContent = "Dang bat live test...";
    return;
  }
  toggleLiveTestButton.disabled = false;
  toggleLiveTestButton.textContent = liveStream ? "Tat live test" : "Bat live test";
}

function findActiveIncident(currentTime) {
  if (!incidentCards.length) {
    return null;
  }
  return incidentCards.find((card) => {
    const marker = Number(card.dataset.incidentTime || 0);
    return currentTime >= marker - incidentLookbackSeconds && currentTime <= marker + incidentDisplayWindowSeconds;
  }) || null;
}

function setStageFlagState(isIncidentActive) {
  if (!stageFlag) {
    return;
  }
  stageFlag.classList.toggle("is-normal", !isIncidentActive);
}

function setStageFlagText(label, isIncidentActive) {
  setStageFlagState(isIncidentActive);
  if (stageFlagText) {
    stageFlagText.textContent = label;
  }
}

function renderIncidentHistory(incidents, emptyText) {
  if (!incidentList) {
    return;
  }

  if (!Array.isArray(incidents) || !incidents.length) {
    incidentList.innerHTML = `
      <article class="incident-card incident-card-empty">
        <small>00:00:00</small>
        <p>${escapeHtml(emptyText)}</p>
      </article>
    `;
    refreshIncidentCards();
    return;
  }

  incidentList.innerHTML = incidents
    .map((incident) => {
      const snapshotMarkup = incident.snapshot_url
        ? `<img class="incident-thumb" src="${incident.snapshot_url}" alt="Snapshot vi pham tai ${escapeHtml(incident.time)}">`
        : "";
      return `
        <article class="incident-card" data-incident-time="${Number(incident.time_seconds || 0)}" data-incident-label="${genericIncidentLabel}">
          <small>${escapeHtml(incident.time || "00:00:00")}</small>
          <p>${genericIncidentLabel}</p>
          ${snapshotMarkup}
        </article>
      `;
    })
    .join("");
  refreshIncidentCards();
}

function setActiveIncident(currentTime) {
  const activeCard = findActiveIncident(currentTime);
  incidentCards.forEach((card) => card.classList.toggle("is-active", card === activeCard));
  if (!stageFlagText) {
    return;
  }
  if (activeCard) {
    setStageFlagText(activeCard.dataset.incidentLabel || genericIncidentLabel, true);
    return;
  }
  setStageFlagText(reviewPayload.video_url ? defaultStageFlagText : emptyStageFlagText, false);
}

function bindIncidentCardNavigation() {
  incidentCards.forEach((card) => {
    card.addEventListener("click", () => {
      if (!reviewVideoPlayer || liveStream) {
        return;
      }
      const marker = Number(card.dataset.incidentTime || 0);
      reviewVideoPlayer.currentTime = marker;
      reviewVideoPlayer.play();
      setActiveIncident(marker);
    });
  });
}

function initializeReviewTimeline() {
  refreshIncidentCards();
  if (incidentCountChip) {
    incidentCountChip.textContent = `${reviewPayload.incidents?.length || 0} su co`;
  }

  if (!incidentCards.length) {
    setStageFlagText(reviewPayload.video_url ? defaultStageFlagText : emptyStageFlagText, false);
    return;
  }

  bindIncidentCardNavigation();
  setActiveIncident(0);

  if (reviewVideoPlayer) {
    reviewVideoPlayer.addEventListener("timeupdate", () => {
      setActiveIncident(reviewVideoPlayer.currentTime);
    });
  }
}

function showLiveCameraStage() {
  if (reviewVideoStage) {
    reviewVideoStage.classList.add("has-video", "is-live");
  }
  if (reviewVideoPlayer) {
    reviewVideoPlayer.pause();
    reviewVideoPlayer.hidden = true;
  }
  if (stagePlaceholder) {
    stagePlaceholder.hidden = true;
  }
  if (liveCameraPlayer) {
    liveCameraPlayer.hidden = false;
  }
}

function restoreRecordedStage() {
  if (reviewVideoStage) {
    reviewVideoStage.classList.remove("is-live");
    if (!reviewPayload.video_url) {
      reviewVideoStage.classList.remove("has-video");
    }
  }
  if (reviewVideoPlayer) {
    reviewVideoPlayer.hidden = false;
  }
  if (stagePlaceholder) {
    stagePlaceholder.hidden = false;
  }
  if (liveCameraPlayer) {
    liveCameraPlayer.hidden = true;
  }
}

function restoreRecordedIncidentHistory() {
  if (!incidentList) {
    return;
  }
  incidentList.innerHTML = initialIncidentMarkup;
  refreshIncidentCards();
  if (incidentCountChip) {
    incidentCountChip.textContent = `${reviewPayload.incidents?.length || 0} su co`;
  }
  if (reviewPayload.video_url) {
    bindIncidentCardNavigation();
    setActiveIncident(reviewVideoPlayer?.currentTime || 0);
  }
}

function stopLiveReviewMode({ restoreState = true } = {}) {
  liveModeStarting = false;
  if (livePollTimer) {
    window.clearInterval(livePollTimer);
    livePollTimer = null;
  }
  livePollInFlight = false;
  if (liveStream) {
    liveStream.getTracks().forEach((track) => track.stop());
    liveStream = null;
  }
  if (liveCameraPlayer) {
    liveCameraPlayer.pause();
    liveCameraPlayer.srcObject = null;
  }
  liveModeStarted = false;
  if (restoreState) {
    restoreRecordedStage();
    restoreRecordedIncidentHistory();
    setStageFlagText(reviewPayload.video_url ? defaultStageFlagText : emptyStageFlagText, false);
  }
  updateLiveToggleButton();
}

function applyLiveReviewPayload(payload) {
  if (incidentCountChip) {
    incidentCountChip.textContent = `${Number(payload?.incident_count || 0)} su co`;
  }
  renderIncidentHistory(payload?.incidents || [], liveWaitingIncidentText);
  if (payload?.stage_state === "alert") {
    setStageFlagText(payload.stage_label || genericIncidentLabel, true);
  } else {
    setStageFlagText(defaultStageFlagText, false);
  }
}

async function postLiveFrame(blob) {
  const formData = new FormData();
  formData.append("frame", blob, "live-frame.jpg");
  const response = await fetch("/review/live/frame", {
    method: "POST",
    body: formData,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.message || "Khong the phan tich frame webcam.");
  }
  return payload;
}

async function sendLiveFrame() {
  if (
    !liveStream
    || !liveCameraPlayer
    || livePollInFlight
    || liveCameraPlayer.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
    || !liveCaptureContext
  ) {
    return;
  }

  const sourceWidth = liveCameraPlayer.videoWidth || 0;
  const sourceHeight = liveCameraPlayer.videoHeight || 0;
  if (!sourceWidth || !sourceHeight) {
    return;
  }

  livePollInFlight = true;
  try {
    const scale = Math.min(1, liveFrameMaxWidth / sourceWidth);
    liveCaptureCanvas.width = Math.max(1, Math.round(sourceWidth * scale));
    liveCaptureCanvas.height = Math.max(1, Math.round(sourceHeight * scale));
    liveCaptureContext.drawImage(liveCameraPlayer, 0, 0, liveCaptureCanvas.width, liveCaptureCanvas.height);
    const blob = await new Promise((resolve) => {
      liveCaptureCanvas.toBlob(resolve, "image/jpeg", 0.82);
    });
    if (!blob) {
      return;
    }
    const payload = await postLiveFrame(blob);
    applyLiveReviewPayload(payload);
  } catch (error) {
    setStageFlagText(error.message || liveCameraDeniedText, false);
  } finally {
    livePollInFlight = false;
  }
}

function startLivePolling() {
  if (livePollTimer) {
    return;
  }
  sendLiveFrame();
  livePollTimer = window.setInterval(() => {
    sendLiveFrame();
  }, liveFrameIntervalMs);
}

async function ensureLiveReviewMode() {
  if (liveStream || liveModeStarting || !liveCameraPlayer) {
    return;
  }
  liveModeStarting = true;
  updateLiveToggleButton();

  if (!navigator.mediaDevices?.getUserMedia) {
    liveModeStarting = false;
    updateLiveToggleButton();
    setLiveTestFeedback("Trinh duyet nay khong ho tro bat webcam live test.", true);
    setStageFlagText(liveCameraDeniedText, false);
    return;
  }

  setStageFlagText(liveCameraRequestText, false);
  renderIncidentHistory([], liveWaitingIncidentText);
  setLiveTestFeedback("Dang bat webcam va khoi tao live test...");

  try {
    const startResponse = await fetch("/review/live/start", { method: "POST" });
    if (!startResponse.ok) {
      const payload = await startResponse.json();
      throw new Error(payload?.message || "Khong the khoi tao live test.");
    }

    liveStream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: "user",
      },
      audio: false,
    });
    liveCameraPlayer.srcObject = liveStream;
    await liveCameraPlayer.play();
    showLiveCameraStage();
    liveModeStarted = true;
    liveModeStarting = false;
    updateLiveToggleButton();
    setStageFlagText(defaultStageFlagText, false);
    setLiveTestFeedback("Live test dang bat. Webcam se duoc phan tich dinh ky de hien canh bao.");
    startLivePolling();
  } catch (error) {
    stopLiveReviewMode();
    setLiveTestFeedback(error.message || liveCameraDeniedText, true);
    setStageFlagText(error.message || liveCameraDeniedText, false);
  }
}

activateTab(appShell?.dataset.initialTab || "overview");
syncStudentTeacherReview();
renderStudents();
initializeReviewTimeline();
syncSelectedFileState();
renderTeacherReview();
updateLiveToggleButton();
setLiveTestFeedback("Bat webcam de test canh bao thoi gian thuc trong khung hinh nay.");

if (confirmFraudButton) {
  confirmFraudButton.addEventListener("click", () => {
    submitTeacherReviewDecision("confirmed");
  });
}

if (dismissReviewButton) {
  dismissReviewButton.addEventListener("click", () => {
    submitTeacherReviewDecision("dismissed");
  });
}

if (toggleLiveTestButton) {
  toggleLiveTestButton.addEventListener("click", () => {
    if (liveStream || liveModeStarting) {
      stopLiveReviewMode();
      setLiveTestFeedback("Da tat live test. Ban co the bat lai bat cu luc nao.");
      return;
    }
    ensureLiveReviewMode();
  });
}

window.addEventListener("beforeunload", () => {
  stopLiveReviewMode({ restoreState: false });
});
