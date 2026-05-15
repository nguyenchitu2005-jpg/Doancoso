const tabs = document.querySelectorAll(".nav-item");
const sections = document.querySelectorAll(".page-section");
const pageTitle = document.getElementById("page-title");
const dashboardPayload = JSON.parse(document.getElementById("dashboard-data").textContent);
const reviewPayloadElement = document.getElementById("review-data");
const reviewPayload = reviewPayloadElement ? JSON.parse(reviewPayloadElement.textContent) : { incidents: [] };
const reviewHistoryPayloadElement = document.getElementById("review-history-data");
const reviewHistory = reviewHistoryPayloadElement ? JSON.parse(reviewHistoryPayloadElement.textContent) : [];
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
const studentsRoomToolbar = document.getElementById("students-room-toolbar");
const studentsBackButton = document.getElementById("students-back-button");
const studentsRoomIndicator = document.getElementById("students-room-indicator");
const roomFilterCards = document.querySelectorAll("[data-room-filter]");
const allStudents = Array.isArray(dashboardPayload.students) ? dashboardPayload.students : [];
let activeRiskFilter = "all";
let activeSearchQuery = "";
let activeRoomFilter = "";

function normalizeCandidateId(value) {
  const candidateId = String(value || "").trim();
  if (!candidateId || candidateId.toUpperCase() === "UNKNOWN") {
    return "";
  }
  return candidateId.toUpperCase();
}

function candidateIdsMatch(left, right) {
  const normalizedLeft = normalizeCandidateId(left);
  const normalizedRight = normalizeCandidateId(right);
  return Boolean(normalizedLeft) && normalizedLeft === normalizedRight;
}

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
      const candidateId = normalizeCandidateId(student?.candidate_id);
      if (candidateId) {
        candidateIds.add(candidateId);
      }
    });
  }
  if (!candidateIds.size) {
    const primaryCandidateId = normalizeCandidateId(reviewPayload.primary_candidate?.candidate_id);
    if (primaryCandidateId) {
      candidateIds.add(primaryCandidateId);
    }
  }
  return candidateIds;
}

function syncStudentTeacherReview() {
  const reviewedCandidateIds = getReviewedCandidateIds();
  allStudents.forEach((student) => {
    const candidateId = normalizeCandidateId(student?.candidate_id);
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
    const currentResultPath = String(reviewPayload.result_path || "");
    const currentVideoPath = String(reviewPayload.video_path || "");
    reviewHistory.forEach((entry) => {
      const sameResult = currentResultPath && String(entry?.result_path || "") === currentResultPath;
      const sameVideo = !currentResultPath && currentVideoPath && String(entry?.video_path || "") === currentVideoPath;
      if (sameResult || sameVideo) {
        entry.teacher_review = payload.teacher_review || {};
      }
    });
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

function roomMatchesEntry(entry, roomFilter) {
  const normalizedRoomFilter = normalizeText(roomFilter);
  if (!normalizedRoomFilter) {
    return true;
  }
  return normalizeText(entry?.student?.room) === normalizedRoomFilter;
}

function getFilteredStudents() {
  return studentSearchIndex
    .filter((entry) => (
      (activeRiskFilter === "all" || entry.student.risk === activeRiskFilter)
      && roomMatchesEntry(entry, activeRoomFilter)
      && searchMatchesEntry(entry, activeSearchQuery)
    ))
    .map((entry) => entry.student);
}

function behaviorChips(behaviors) {
  if (!Array.isArray(behaviors) || !behaviors.length) {
    return '<span class="table-chip">Không có</span>';
  }
  return behaviors.map((behavior) => `<span class="table-chip">${escapeHtml(behavior)}</span>`).join("");
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
    const studentName = String(student.name || "Unknown Candidate");
    const studentEmail = String(student.email || "");
    const studentCandidateId = String(student.candidate_id || "");
    const studentRoom = String(student.room || "");
    const studentRisk = String(student.risk || "low");
    const studentRiskLabel = riskLabels[studentRisk] || riskLabels.low;
    return `
    <tr>
      <td>
        <div class="candidate-cell">
          <span class="table-avatar">${studentName.trim().split(/\s+/).slice(-1)[0].slice(0, 2).toUpperCase()}</span>
          <div>
            <a
              class="student-review-link"
              href="/?tab=review&review_candidate_id=${encodeURIComponent(studentCandidateId)}"
              data-open-student-review="true"
              data-candidate-id="${escapeHtml(studentCandidateId)}"
              title="Mo hau kiem cua ${escapeHtml(studentName)}"
            >${escapeHtml(studentName)}</a>
            <small>${escapeHtml(studentEmail)}</small>
          </div>
        </div>
      </td>
      <td>
        <div class="cell-stack">
          <strong>${escapeHtml(studentCandidateId)}</strong>
          <small>${escapeHtml(studentRoom)}</small>
        </div>
      </td>
      <td><div class="table-chip-row">${behaviorChips(student.behaviors)}</div></td>
      <td><strong>${student.alerts}</strong></td>
      <td><span class="risk-badge risk-${studentRisk}">${studentRiskLabel}</span></td>
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

function setStudentRiskFilter(nextRisk) {
  activeRiskFilter = nextRisk || "all";
  riskChips.forEach((chip) => {
    chip.classList.toggle("is-selected", (chip.dataset.risk || "all") === activeRiskFilter);
  });
}

function syncStudentsRoomToolbar() {
  const hasRoomFilter = Boolean(activeRoomFilter);
  if (studentsRoomToolbar) {
    studentsRoomToolbar.hidden = !hasRoomFilter;
  }
  if (studentsRoomIndicator) {
    studentsRoomIndicator.textContent = hasRoomFilter ? `Dang xem phong: ${activeRoomFilter}` : "";
  }
}

function applyStudentSearch(query, { openStudentsTab = false, resetRiskFilter = false } = {}) {
  activeSearchQuery = String(query || "").trim();
  if (globalSearchInput) {
    globalSearchInput.value = activeSearchQuery;
  }
  if (resetRiskFilter) {
    setStudentRiskFilter("all");
  }
  updateSearchSuggestions(activeSearchQuery);
  if (openStudentsTab) {
    activateTab("students");
  }
  renderStudents();
}

function applyRoomFilter(roomName, { openStudentsTab = true } = {}) {
  activeRoomFilter = String(roomName || "").trim();
  syncStudentsRoomToolbar();
  applyStudentSearch("", { openStudentsTab, resetRiskFilter: true });
}

function clearRoomFilterAndReturn() {
  activeRoomFilter = "";
  syncStudentsRoomToolbar();
  applyStudentSearch("", { openStudentsTab: false, resetRiskFilter: true });

  const nextUrl = new URL(window.location.href);
  nextUrl.searchParams.delete("students_room");
  nextUrl.searchParams.delete("review_candidate_id");
  nextUrl.searchParams.set("tab", "overview");
  window.history.replaceState({}, "", `${nextUrl.pathname}?${nextUrl.searchParams.toString()}`);
  activateTab("overview");
}

riskChips.forEach((chip) => {
  chip.addEventListener("click", () => {
    setStudentRiskFilter(chip.dataset.risk || "all");
    renderStudents();
  });
});

if (globalSearchInput) {
  globalSearchInput.addEventListener("input", () => {
    applyStudentSearch(globalSearchInput.value || "", { openStudentsTab: false });
  });

  globalSearchInput.addEventListener("change", () => {
    applyStudentSearch(globalSearchInput.value || "", { openStudentsTab: false });
  });

  globalSearchInput.addEventListener("search", () => {
    applyStudentSearch(globalSearchInput.value || "", { openStudentsTab: false });
  });

  globalSearchInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    applyStudentSearch(globalSearchInput.value || "", { openStudentsTab: true });
  });
}

if (exportStudentsCsvButton) {
  exportStudentsCsvButton.addEventListener("click", downloadStudentsReport);
}

if (studentsBackButton) {
  studentsBackButton.addEventListener("click", () => {
    clearRoomFilterAndReturn();
  });
}

if (studentTableBody) {
  studentTableBody.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-open-student-review]");
    if (!trigger) {
      return;
    }
    event.preventDefault();
    const candidateId = String(trigger.dataset.candidateId || "");
    const student = allStudents.find((item) => candidateIdsMatch(item?.candidate_id, candidateId));
    if (!student) {
      window.alert("Khong tim thay du lieu thi sinh de mo hau kiem.");
      return;
    }
    openStudentReview(student);
  });
}

roomFilterCards.forEach((card) => {
  card.addEventListener("click", (event) => {
    const roomName = String(card.dataset.roomFilter || "").trim();
    if (!roomName) {
      return;
    }
    event.preventDefault();
    applyRoomFilter(roomName, { openStudentsTab: true });
  });
});

const confidenceRange = document.getElementById("confidence-range");
const confidenceValue = document.getElementById("confidence-value");
const phoneConfidenceRange = document.getElementById("phone-confidence-range");
const phoneConfidenceValue = document.getElementById("phone-confidence-value");
const intervalRange = document.getElementById("interval-range");
const intervalValue = document.getElementById("interval-value");
const behaviorThresholdRange = document.getElementById("behavior-threshold-range");
const behaviorThresholdValue = document.getElementById("behavior-threshold-value");
const toggleGazeAlerts = document.getElementById("toggle-gaze-alerts");
const toggleCellPhoneAlerts = document.getElementById("toggle-cell-phone-alerts");
const toggleFaceMissingAlerts = document.getElementById("toggle-face-missing-alerts");
const toggleMultiplePeopleAlerts = document.getElementById("toggle-multiple-people-alerts");
const saveSettingsButton = document.getElementById("save-settings-button");
const resetSettingsButton = document.getElementById("reset-settings-button");
const settingsFeedback = document.getElementById("settings-feedback");

function patchToggleLabels() {
  const faceMissingRow = toggleFaceMissingAlerts?.closest(".toggle-row");
  const multiplePeopleRow = toggleMultiplePeopleAlerts?.closest(".toggle-row");
  const faceMissingTitle = faceMissingRow?.querySelector("p");
  const faceMissingDescription = faceMissingRow?.querySelector("small");
  const multiplePeopleTitle = multiplePeopleRow?.querySelector("p");
  const multiplePeopleDescription = multiplePeopleRow?.querySelector("small");

  if (faceMissingTitle) {
    faceMissingTitle.textContent = "Vắng mặt khỏi khung hình";
  }
  if (faceMissingDescription) {
    faceMissingDescription.textContent = "Cảnh báo khi không còn thấy khuôn mặt thí sinh.";
  }
  if (multiplePeopleTitle) {
    multiplePeopleTitle.textContent = "Trao đổi nhóm";
  }
  if (multiplePeopleDescription) {
    multiplePeopleDescription.textContent = "Phát hiện nhiều người trong khung hình.";
  }
}

patchToggleLabels();

if (confidenceRange && confidenceValue) {
  confidenceRange.addEventListener("input", () => {
    confidenceValue.textContent = Number(confidenceRange.value).toFixed(2);
  });
}

if (phoneConfidenceRange && phoneConfidenceValue) {
  phoneConfidenceRange.addEventListener("input", () => {
    phoneConfidenceValue.textContent = Number(phoneConfidenceRange.value).toFixed(2);
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
    phone_conf_threshold: Number(phoneConfidenceRange?.value || 0.30),
    extraction_interval_seconds: Number(intervalRange?.value || 0.5),
    behavior_threshold: Number(behaviorThresholdRange?.value || 0.82),
    enable_gaze_alerts: Boolean(toggleGazeAlerts?.checked),
    enable_cell_phone_alerts: Boolean(toggleCellPhoneAlerts?.checked),
    enable_face_missing_alerts: Boolean(toggleFaceMissingAlerts?.checked),
    enable_multiple_people_alerts: Boolean(toggleMultiplePeopleAlerts?.checked),
  };
}

function applyAiSettings(settings) {
  if (confidenceRange && confidenceValue && typeof settings.confidence_threshold === "number") {
    confidenceRange.value = settings.confidence_threshold.toFixed(2);
    confidenceValue.textContent = settings.confidence_threshold.toFixed(2);
  }
  if (phoneConfidenceRange && phoneConfidenceValue && typeof settings.phone_conf_threshold === "number") {
    phoneConfidenceRange.value = settings.phone_conf_threshold.toFixed(2);
    phoneConfidenceValue.textContent = settings.phone_conf_threshold.toFixed(2);
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
  if (toggleFaceMissingAlerts && typeof settings.enable_face_missing_alerts === "boolean") {
    toggleFaceMissingAlerts.checked = settings.enable_face_missing_alerts;
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
const uploadForm = document.querySelector(".upload-form");
const uploadTokenInput = document.getElementById("upload-token-input");
const uploadFileInput = document.getElementById("review-video-file");
const uploadDropzone = document.querySelector(".upload-dropzone");
const selectedFileName = document.getElementById("selected-file-name");
const clearSelectedFileButton = document.getElementById("clear-selected-file");
const uploadSubmitButton = document.getElementById("upload-submit-button");
const uploadProgressBlock = document.getElementById("upload-progress-block");
const uploadProgressLabel = document.getElementById("upload-progress-label");
const uploadProgressValue = document.getElementById("upload-progress-value");
const uploadProgressTrack = document.querySelector(".upload-progress-track");
const uploadProgressBar = document.getElementById("upload-progress-bar");
const candidateImageInput = document.getElementById("candidate-image-file");
const candidateImageName = document.getElementById("candidate-image-name");
const candidateSubmitButton = document.getElementById("candidate-submit-button");
const candidateClearButton = document.getElementById("candidate-clear-button");
const candidateDeleteButton = document.getElementById("candidate-delete-button");
const candidateDeleteForm = document.getElementById("candidate-delete-form");
const candidateDeleteIdInput = document.getElementById("candidate-delete-id-input");
const candidateIdInput = document.getElementById("candidate-id-input");
const candidateNameInput = document.getElementById("candidate-name-input");
const candidateEmailInput = document.getElementById("candidate-email-input");
const candidateRoomInput = document.getElementById("candidate-room-input");
const candidateRegistryItems = document.querySelectorAll(".candidate-registry-item[data-candidate-id]");
let selectedFaceCandidateId = "";
let selectedFaceCandidateImage = "";
let selectedReviewVideoFiles = [];
let uploadInProgress = false;
let uploadProgressPollTimer = null;

const acceptedReviewVideoExtensions = new Set(["mp4", "avi", "mov", "mkv"]);

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

  const files = selectedReviewVideoFiles.length
    ? selectedReviewVideoFiles
    : Array.from(uploadFileInput.files || []);
  const fileCount = files.length;
  const hasFile = fileCount > 0;

  if (!hasFile) {
    selectedFileName.textContent = "Chua chon video nao";
  } else if (fileCount === 1) {
    selectedFileName.textContent = `Da chon: ${files[0].name}`;
  } else {
    const previewNames = files
      .slice(0, 3)
      .map((file) => file.name)
      .join(", ");
    const remainingCount = fileCount - Math.min(fileCount, 3);
    const suffix = remainingCount > 0 ? ` (+${remainingCount})` : "";
    selectedFileName.textContent = `Da chon ${fileCount} video: ${previewNames}${suffix}`;
  }

  if (clearSelectedFileButton) {
    clearSelectedFileButton.hidden = !hasFile;
  }

  if (uploadSubmitButton) {
    uploadSubmitButton.disabled = !hasFile || uploadInProgress;
  }
}

function setUploadProgress(percent, label) {
  const normalizedPercent = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
  const cleanedLabel = String(label || "Dang tai len...").replace(/\s*:?\s*\d{1,3}%\s*$/, "");
  if (uploadProgressBlock) {
    uploadProgressBlock.hidden = false;
  }
  if (uploadProgressLabel) {
    uploadProgressLabel.textContent = cleanedLabel;
  }
  if (uploadProgressValue) {
    uploadProgressValue.textContent = `${normalizedPercent}%`;
  }
  if (uploadProgressBar) {
    uploadProgressBar.style.width = `${normalizedPercent}%`;
  }
  if (uploadProgressTrack) {
    uploadProgressTrack.setAttribute("aria-valuenow", String(normalizedPercent));
  }
}

function createUploadToken() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function stopUploadProgressPolling() {
  if (uploadProgressPollTimer) {
    window.clearInterval(uploadProgressPollTimer);
    uploadProgressPollTimer = null;
  }
}

async function pollUploadProcessingProgress(uploadToken) {
  if (!uploadToken) {
    return;
  }
  try {
    const response = await fetch(`/review/upload/progress/${encodeURIComponent(uploadToken)}`, {
      cache: "no-store",
    });
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    const percent = Number(payload?.percent || 0);
    const message = String(payload?.message || "Dang xu ly video...");
    setUploadProgress(percent, message);
    if (payload?.stage === "completed" || payload?.stage === "error") {
      stopUploadProgressPolling();
    }
  } catch (error) {
    // Polling is best-effort; the main upload request still controls final navigation.
  }
}

function startUploadProgressPolling(uploadToken) {
  stopUploadProgressPolling();
  pollUploadProcessingProgress(uploadToken);
  uploadProgressPollTimer = window.setInterval(() => {
    pollUploadProcessingProgress(uploadToken);
  }, 700);
}

function resetUploadProgress() {
  stopUploadProgressPolling();
  if (uploadProgressBlock) {
    uploadProgressBlock.hidden = true;
  }
  setUploadProgress(0, "Dang tai len...");
  if (uploadProgressBlock) {
    uploadProgressBlock.hidden = true;
  }
}

function setUploadControlsDisabled(disabled) {
  uploadInProgress = Boolean(disabled);
  if (uploadFileInput) {
    uploadFileInput.disabled = uploadInProgress;
  }
  uploadTriggers.forEach((trigger) => {
    if (trigger.dataset.fileTrigger === "review-video-file") {
      trigger.disabled = uploadInProgress;
    }
  });
  if (clearSelectedFileButton) {
    clearSelectedFileButton.disabled = uploadInProgress;
  }
  syncSelectedFileState();
}

function getReviewVideoKey(file) {
  return [file.name, file.size, file.lastModified].join("|");
}

function isAcceptedReviewVideo(file) {
  const extension = String(file.name || "").split(".").pop().toLowerCase();
  return acceptedReviewVideoExtensions.has(extension);
}

function writeReviewVideoFilesToInput() {
  if (!uploadFileInput || typeof DataTransfer === "undefined") {
    return;
  }

  const transfer = new DataTransfer();
  selectedReviewVideoFiles.forEach((file) => {
    transfer.items.add(file);
  });
  uploadFileInput.files = transfer.files;
}

function addReviewVideoFiles(files) {
  const existingFiles = new Map(selectedReviewVideoFiles.map((file) => [getReviewVideoKey(file), file]));

  Array.from(files || []).forEach((file) => {
    if (!isAcceptedReviewVideo(file)) {
      return;
    }

    existingFiles.set(getReviewVideoKey(file), file);
  });

  selectedReviewVideoFiles = Array.from(existingFiles.values());
  writeReviewVideoFilesToInput();
  syncSelectedFileState();
}

if (uploadFileInput && selectedFileName) {
  uploadFileInput.addEventListener("change", () => {
    addReviewVideoFiles(uploadFileInput.files);
  });
}

if (clearSelectedFileButton && uploadFileInput) {
  clearSelectedFileButton.addEventListener("click", () => {
    selectedReviewVideoFiles = [];
    uploadFileInput.value = "";
    resetUploadProgress();
    syncSelectedFileState();
  });
}

if (uploadDropzone) {
  uploadDropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    uploadDropzone.classList.add("is-drag-over");
  });

  uploadDropzone.addEventListener("dragleave", () => {
    uploadDropzone.classList.remove("is-drag-over");
  });

  uploadDropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    uploadDropzone.classList.remove("is-drag-over");
    addReviewVideoFiles(event.dataTransfer?.files || []);
  });
}

if (uploadForm && uploadFileInput) {
  uploadForm.addEventListener("submit", (event) => {
    event.preventDefault();

    const hasFiles = uploadFileInput.files && uploadFileInput.files.length > 0;
    if (!hasFiles || uploadInProgress) {
      return;
    }

    const request = new XMLHttpRequest();
    const uploadToken = createUploadToken();
    if (uploadTokenInput) {
      uploadTokenInput.value = uploadToken;
    }
    const formData = new FormData(uploadForm);

    setUploadControlsDisabled(true);
    setUploadProgress(0, "Dang tai len video...");

    request.upload.addEventListener("progress", (progressEvent) => {
      if (!progressEvent.lengthComputable) {
        return;
      }
      const percent = (progressEvent.loaded / progressEvent.total) * 100;
      setUploadProgress(percent, "Dang tai len video...");
    });

    request.upload.addEventListener("load", () => {
      setUploadProgress(0, "Da tai len, dang bat dau xu ly video...");
      if (uploadSubmitButton) {
        uploadSubmitButton.textContent = "Dang xu ly video...";
      }
      startUploadProgressPolling(uploadToken);
    });

    request.addEventListener("load", () => {
      stopUploadProgressPolling();
      if (request.status >= 200 && request.status < 400) {
        setUploadProgress(100, "Hoan tat xu ly video.");
        window.location.href = request.responseURL || "/?tab=review";
        return;
      }
      setUploadProgress(0, "Tai len that bai. Vui long thu lai.");
      setUploadControlsDisabled(false);
      if (uploadSubmitButton) {
        uploadSubmitButton.textContent = "Tai va xu ly video";
      }
    });

    request.addEventListener("error", () => {
      stopUploadProgressPolling();
      setUploadProgress(0, "Khong the tai len video. Kiem tra ket noi va thu lai.");
      setUploadControlsDisabled(false);
      if (uploadSubmitButton) {
        uploadSubmitButton.textContent = "Tai va xu ly video";
      }
    });

    request.addEventListener("abort", () => {
      stopUploadProgressPolling();
      resetUploadProgress();
      setUploadControlsDisabled(false);
      if (uploadSubmitButton) {
        uploadSubmitButton.textContent = "Tai va xu ly video";
      }
    });

    request.open(uploadForm.method || "POST", uploadForm.action);
    request.send(formData);
  });
}

function syncCandidateImageState() {
  if (!candidateImageInput || !candidateImageName) {
    return;
  }
  const file = candidateImageInput.files?.[0] || null;
  if (file) {
    candidateImageName.textContent = `Da chon anh moi: ${file.name}`;
  } else if (selectedFaceCandidateImage) {
    candidateImageName.textContent = `Dang dung anh hien tai: ${selectedFaceCandidateImage}`;
  } else {
    candidateImageName.textContent = "Chua chon anh nao";
  }
  if (candidateSubmitButton) {
    candidateSubmitButton.disabled = !file && !selectedFaceCandidateId;
  }
}

if (candidateImageInput) {
  candidateImageInput.addEventListener("change", syncCandidateImageState);
  syncCandidateImageState();
}

function setCandidateFormMode(candidate) {
  selectedFaceCandidateId = candidate?.candidateId || "";
  selectedFaceCandidateImage = candidate?.image || "";
  if (candidateIdInput) {
    candidateIdInput.value = candidate?.candidateId || "";
  }
  if (candidateNameInput) {
    candidateNameInput.value = candidate?.name || "";
  }
  if (candidateEmailInput) {
    candidateEmailInput.value = candidate?.email || "";
  }
  if (candidateRoomInput) {
    candidateRoomInput.value = candidate?.room || "";
  }
  if (candidateImageInput) {
    candidateImageInput.value = "";
  }
  if (candidateSubmitButton) {
    candidateSubmitButton.textContent = selectedFaceCandidateId ? "Cập nhật thí sinh" : "Thêm thí sinh";
  }
  if (candidateClearButton) {
    candidateClearButton.hidden = !selectedFaceCandidateId;
  }
  if (candidateDeleteButton) {
    candidateDeleteButton.hidden = !selectedFaceCandidateId;
  }
  if (candidateDeleteIdInput) {
    candidateDeleteIdInput.value = selectedFaceCandidateId;
  }
  candidateRegistryItems.forEach((item) => {
    item.classList.toggle("is-selected", item.dataset.candidateId === selectedFaceCandidateId);
  });
  syncCandidateImageState();
}

candidateRegistryItems.forEach((item) => {
  item.addEventListener("click", () => {
    setCandidateFormMode({
      candidateId: item.dataset.candidateId || "",
      name: item.dataset.candidateName || "",
      email: item.dataset.candidateEmail || "",
      room: item.dataset.candidateRoom || "",
      image: item.dataset.candidateImage || "",
    });
  });
});

if (candidateClearButton) {
  candidateClearButton.addEventListener("click", () => {
    setCandidateFormMode(null);
  });
}

if (candidateDeleteButton && candidateDeleteForm) {
  candidateDeleteButton.addEventListener("click", () => {
    if (!selectedFaceCandidateId) {
      return;
    }
    const candidateName = candidateNameInput?.value || selectedFaceCandidateId;
    const confirmed = window.confirm(`Xoa thi sinh ${candidateName}? Hanh dong nay se xoa ca anh mau.`);
    if (!confirmed) {
      return;
    }
    candidateDeleteForm.submit();
  });
}

const reviewVideoPlayer = document.getElementById("review-video-player");
const liveCameraPlayer = document.getElementById("live-camera-player");
const reviewVideoStage = document.getElementById("review-video-stage");
const stagePlaceholder = document.getElementById("review-stage-placeholder");
const reviewSessionList = document.getElementById("review-session-list");
const reviewCandidateAvatar = document.getElementById("review-candidate-avatar");
const reviewCandidateName = document.getElementById("review-candidate-name");
const reviewCandidateId = document.getElementById("review-candidate-id");
const reviewCandidateEmail = document.getElementById("review-candidate-email");
const reviewCandidateRoom = document.getElementById("review-candidate-room");
const reviewCandidateAlerts = document.getElementById("review-candidate-alerts");
const reviewCandidateDeviceStatus = document.getElementById("review-candidate-device-status");
const reviewRiskLabel = document.getElementById("review-risk-label");
const reviewRiskMessage = document.getElementById("review-risk-message");
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
const liveFrameIntervalMs = 300;
const liveFrameMaxWidth = 960;
const liveCaptureCanvas = document.createElement("canvas");
const liveCaptureContext = liveCaptureCanvas.getContext("2d");
let liveModeStarted = false;
let liveModeStarting = false;
let liveStream = null;
let livePollTimer = null;
let livePollInFlight = false;
let selectedReviewCandidateId = "";
let selectedReviewStudentContext = null;
let reviewJumpHighlightTimer = null;

function replaceReviewPayload(nextPayload) {
  Object.keys(reviewPayload).forEach((key) => {
    delete reviewPayload[key];
  });
  Object.assign(reviewPayload, nextPayload || { incidents: [] });
}

function deriveReviewCandidate(payload) {
  const candidate = payload?.review_candidate || payload?.primary_candidate || {};
  const name = String(candidate?.name || "Unknown Candidate");
  const parts = name.split(/\s+/).filter(Boolean).slice(0, 2);
  return {
    candidate_id: String(candidate?.candidate_id || "UNKNOWN"),
    name,
    email: String(candidate?.email || ""),
    room: String(candidate?.room || ""),
    alerts: Number(candidate?.alerts || 0),
    risk_label: String(candidate?.risk_label || "THAP"),
    device_status: String(candidate?.device_status || "Dang cho phan tich"),
    avatar: String(candidate?.avatar || parts.map((part) => part[0]).join("").toUpperCase() || "UC"),
  };
}

function deriveReviewCandidateFromStudent(student, fallbackCandidate) {
  const fallback = fallbackCandidate || deriveReviewCandidate(reviewPayload);
  const studentName = String(student?.name || fallback.name || "Unknown Candidate");
  const parts = studentName.split(/\s+/).filter(Boolean).slice(0, 2);
  const riskValue = String(student?.risk || "").trim().toLowerCase();
  return {
    candidate_id: String(student?.candidate_id || fallback.candidate_id || "UNKNOWN"),
    name: studentName,
    email: String(student?.email || fallback.email || ""),
    room: String(student?.room || fallback.room || ""),
    alerts: Number(student?.alerts ?? fallback.alerts ?? 0),
    risk_label: riskValue ? (riskLabels[riskValue] || String(student?.risk || "").toUpperCase()) : fallback.risk_label,
    device_status: String(student?.device_status || fallback.device_status || "Dang cho phan tich"),
    avatar: String(student?.avatar || parts.map((part) => part[0]).join("").toUpperCase() || fallback.avatar || "UC"),
  };
}

function findStudentInReviewPayload(payload, candidateId) {
  const targetCandidateId = normalizeCandidateId(candidateId);
  if (!targetCandidateId) {
    return null;
  }
  const students = Array.isArray(payload?.students_report) ? payload.students_report : [];
  return students.find((student) => candidateIdsMatch(student?.candidate_id, targetCandidateId)) || null;
}

function reviewContainsCandidate(payload, candidateId) {
  const targetCandidateId = normalizeCandidateId(candidateId);
  if (!targetCandidateId) {
    return false;
  }
  if (findStudentInReviewPayload(payload, targetCandidateId)) {
    return true;
  }
  if (candidateIdsMatch(payload?.primary_candidate?.candidate_id, targetCandidateId)) {
    return true;
  }
  const incidents = Array.isArray(payload?.incidents) ? payload.incidents : [];
  return incidents.some((incident) => candidateIdsMatch(incident?.candidate_id, targetCandidateId));
}

function syncSelectedReviewCandidate(payload, options = {}) {
  const requestedCandidateId = normalizeCandidateId(options?.candidateId);
  if (requestedCandidateId && reviewContainsCandidate(payload, requestedCandidateId)) {
    selectedReviewCandidateId = requestedCandidateId;
    selectedReviewStudentContext = findStudentInReviewPayload(payload, requestedCandidateId) || { ...(options?.student || {}) };
    return;
  }
  selectedReviewCandidateId = "";
  selectedReviewStudentContext = null;
}

function getVisibleReviewIncidents(payload) {
  const incidents = Array.isArray(payload?.incidents) ? payload.incidents : [];
  if (!selectedReviewCandidateId) {
    return incidents;
  }
  const filteredIncidents = incidents.filter((incident) => candidateIdsMatch(incident?.candidate_id, selectedReviewCandidateId));
  return filteredIncidents.length ? filteredIncidents : incidents;
}

function getActiveReviewCandidate(payload) {
  const defaultCandidate = deriveReviewCandidate(payload);
  if (!selectedReviewCandidateId) {
    return defaultCandidate;
  }
  const matchedStudent = findStudentInReviewPayload(payload, selectedReviewCandidateId);
  if (matchedStudent) {
    return deriveReviewCandidateFromStudent(matchedStudent, defaultCandidate);
  }
  if (selectedReviewStudentContext && candidateIdsMatch(selectedReviewStudentContext.candidate_id, selectedReviewCandidateId)) {
    return deriveReviewCandidateFromStudent(selectedReviewStudentContext, defaultCandidate);
  }
  return defaultCandidate;
}

function getReviewRiskMessage(payload) {
  const visibleIncidents = getVisibleReviewIncidents(payload);
  if (selectedReviewCandidateId) {
    return visibleIncidents.length > 0
      ? `He thong da ghi nhan ${visibleIncidents.length} su co cho thi sinh nay trong lan hau kiem nay.`
      : "Chua ghi nhan su co cho thi sinh nay trong lan hau kiem nay.";
  }
  if (payload?.review_risk_message) {
    return String(payload.review_risk_message);
  }
  return visibleIncidents.length > 0
    ? `He thong da ghi nhan ${visibleIncidents.length} su co trong lan hau kiem nay.`
    : "Chua ghi nhan su co trong lan hau kiem nay.";
}

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

function updateReviewDecisionButtons() {
  const isDisabled = !reviewPayload.result_path && !reviewPayload.video_path;
  if (confirmFraudButton) {
    confirmFraudButton.disabled = isDisabled;
  }
  if (dismissReviewButton) {
    dismissReviewButton.disabled = isDisabled;
  }
}

function renderReviewCandidatePanel() {
  const candidate = getActiveReviewCandidate(reviewPayload);
  if (reviewCandidateAvatar) {
    reviewCandidateAvatar.textContent = candidate.avatar;
  }
  if (reviewCandidateName) {
    reviewCandidateName.textContent = candidate.name;
  }
  if (reviewCandidateId) {
    reviewCandidateId.textContent = `Ma sinh vien: #${candidate.candidate_id}`;
  }
  if (reviewCandidateEmail) {
    reviewCandidateEmail.textContent = candidate.email || "N/A";
  }
  if (reviewCandidateRoom) {
    reviewCandidateRoom.textContent = candidate.room || "Unknown Room";
  }
  if (reviewCandidateAlerts) {
    reviewCandidateAlerts.textContent = String(candidate.alerts);
  }
  if (reviewCandidateDeviceStatus) {
    reviewCandidateDeviceStatus.textContent = candidate.device_status;
  }
  if (reviewRiskLabel) {
    reviewRiskLabel.textContent = candidate.risk_label;
  }
  if (reviewRiskMessage) {
    reviewRiskMessage.textContent = getReviewRiskMessage(reviewPayload);
  }
}

function renderRecordedVideoStage() {
  const videoUrl = String(reviewPayload.video_url || "");
  if (reviewVideoStage) {
    reviewVideoStage.classList.toggle("has-video", Boolean(videoUrl));
  }
  if (reviewVideoPlayer) {
    if (videoUrl) {
      if (reviewVideoPlayer.getAttribute("src") !== videoUrl) {
        reviewVideoPlayer.setAttribute("src", videoUrl);
        reviewVideoPlayer.load();
      }
      reviewVideoPlayer.hidden = false;
    } else {
      reviewVideoPlayer.pause();
      reviewVideoPlayer.removeAttribute("src");
      reviewVideoPlayer.load();
      reviewVideoPlayer.hidden = true;
    }
  }
  if (stagePlaceholder) {
    stagePlaceholder.hidden = Boolean(videoUrl);
  }
  setStageFlagText(videoUrl ? defaultStageFlagText : emptyStageFlagText, false);
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
        ? `<img class="incident-thumb" src="${incident.snapshot_url}" alt="Snapshot vi pham tai ${escapeHtml(incident.time)}" loading="lazy" decoding="async">`
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
  bindIncidentCardNavigation();
}

function renderRecordedIncidentHistory() {
  const visibleIncidents = getVisibleReviewIncidents(reviewPayload);
  renderIncidentHistory(visibleIncidents, "Chua co du lieu vi pham. Tai video de bat dau hau kiem.");
  if (incidentCountChip) {
    incidentCountChip.textContent = `${visibleIncidents.length || 0} su co`;
  }
  if (reviewPayload.video_url && reviewVideoPlayer && !reviewVideoPlayer.hidden) {
    setActiveIncident(reviewVideoPlayer.currentTime || 0);
  } else {
    setStageFlagText(reviewPayload.video_url ? defaultStageFlagText : emptyStageFlagText, false);
  }
}

function renderReviewSessionSelection() {
  if (!reviewSessionList) {
    return;
  }
  const selectedResultPath = String(reviewPayload.result_path || "");
  const selectedVideoPath = String(reviewPayload.video_path || "");
  reviewSessionList.querySelectorAll(".review-session-item").forEach((item) => {
    const itemResultPath = String(item.dataset.resultPath || "");
    const itemVideoPath = String(item.dataset.videoPath || "");
    const sameResult = Boolean(selectedResultPath) && itemResultPath === selectedResultPath;
    const sameVideo = !selectedResultPath && Boolean(selectedVideoPath) && itemVideoPath === selectedVideoPath;
    item.classList.toggle("is-active", sameResult || sameVideo);
  });
}

function applyRecordedReviewPayload(nextPayload, options = {}) {
  if (liveStream || liveModeStarting) {
    stopLiveReviewMode({ restoreState: false });
  }
  syncSelectedReviewCandidate(nextPayload, options);
  replaceReviewPayload(nextPayload);
  renderRecordedVideoStage();
  renderRecordedIncidentHistory();
  renderReviewCandidatePanel();
  renderTeacherReview();
  updateReviewDecisionButtons();
  renderReviewSessionSelection();
  syncStudentTeacherReview();
  renderStudents();
}

function focusTeacherReviewDecisionCard() {
  if (!teacherReviewCard) {
    return;
  }
  teacherReviewCard.scrollIntoView({ behavior: "smooth", block: "center" });
  teacherReviewCard.classList.add("is-jump-highlight");
  window.clearTimeout(reviewJumpHighlightTimer);
  reviewJumpHighlightTimer = window.setTimeout(() => {
    teacherReviewCard.classList.remove("is-jump-highlight");
  }, 1800);
}

function upsertReviewHistoryEntry(payload) {
  const selectedResultPath = String(payload?.result_path || "");
  const selectedVideoPath = String(payload?.video_path || "");
  const existingIndex = reviewHistory.findIndex((entry) => {
    const sameResult = selectedResultPath && String(entry?.result_path || "") === selectedResultPath;
    const sameVideo = !selectedResultPath && selectedVideoPath && String(entry?.video_path || "") === selectedVideoPath;
    return sameResult || sameVideo;
  });
  if (existingIndex >= 0) {
    reviewHistory[existingIndex] = payload;
    return;
  }
  reviewHistory.unshift(payload);
}

async function openStudentReview(student) {
  const candidateId = normalizeCandidateId(student?.candidate_id);
  if (!candidateId) {
    window.alert("Khong tim thay ma thi sinh hop le de mo hau kiem.");
    return;
  }

  activateTab("review");

  let targetPayload = reviewContainsCandidate(reviewPayload, candidateId) ? { ...reviewPayload } : null;
  if (!targetPayload) {
    targetPayload = reviewHistory.find((entry) => reviewContainsCandidate(entry, candidateId)) || null;
  }

  if (!targetPayload) {
    showTeacherReviewFeedback("Dang mo lan hau kiem cua thi sinh...");
    try {
      const response = await fetch(`/review/candidate/${encodeURIComponent(candidateId)}`);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload?.message || "Khong tim thay lan hau kiem phu hop.");
      }
      targetPayload = payload.review || null;
      if (!targetPayload) {
        throw new Error("Khong nhan duoc du lieu hau kiem hop le.");
      }
      upsertReviewHistoryEntry(targetPayload);
    } catch (error) {
      showTeacherReviewFeedback(error.message || "Khong tim thay lan hau kiem phu hop.", true);
      return;
    }
  }

  applyRecordedReviewPayload(targetPayload, { candidateId, student });
  showTeacherReviewFeedback(`Da mo lan hau kiem gan nhat cua ${String(student?.name || "thi sinh")}.`);
  focusTeacherReviewDecisionCard();
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
  renderRecordedVideoStage();
  renderRecordedIncidentHistory();
  renderReviewCandidatePanel();
  updateReviewDecisionButtons();
  renderReviewSessionSelection();

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
  renderRecordedIncidentHistory();
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

if (reviewSessionList && Array.isArray(reviewHistory) && reviewHistory.length) {
  reviewSessionList.querySelectorAll(".review-session-item").forEach((item) => {
    item.addEventListener("click", () => {
      const resultPath = String(item.dataset.resultPath || "");
      const videoPath = String(item.dataset.videoPath || "");
      const targetPayload = reviewHistory.find((entry) => {
        return String(entry?.result_path || "") === resultPath || String(entry?.video_path || "") === videoPath;
      });
      if (!targetPayload) {
        return;
      }
      applyRecordedReviewPayload(targetPayload);
    });
  });
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
        width: { ideal: 1280 },
        height: { ideal: 720 },
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
syncStudentsRoomToolbar();
const initialStudentsRoom = new URLSearchParams(window.location.search).get("students_room");
if (initialStudentsRoom) {
  applyRoomFilter(initialStudentsRoom, { openStudentsTab: true });
}
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
