const tabs = document.querySelectorAll(".nav-item");
const sections = document.querySelectorAll(".page-section");
const pageTitle = document.getElementById("page-title");
const dashboardPayload = JSON.parse(document.getElementById("dashboard-data").textContent);
const reviewPayloadElement = document.getElementById("review-data");
const reviewPayload = reviewPayloadElement ? JSON.parse(reviewPayloadElement.textContent) : { incidents: [] };
const appShell = document.querySelector(".app-shell");

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

const studentTableBody = document.getElementById("student-table-body");
const studentTableSummary = document.getElementById("student-table-summary");
const riskChips = document.querySelectorAll(".filter-chip");
const exportStudentsCsvButton = document.getElementById("export-students-csv");

function behaviorChips(behaviors) {
  return behaviors.map((behavior) => `<span class="table-chip">${behavior}</span>`).join("");
}

function renderStudents(filter = "all") {
  const rows = dashboardPayload.students.filter((student) => filter === "all" || student.risk === filter);

  studentTableBody.innerHTML = rows.map((student) => `
    <tr>
      <td>
        <div class="candidate-cell">
          <span class="table-avatar">${student.name.split(" ").slice(-1)[0].slice(0, 2).toUpperCase()}</span>
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
      <td><button class="text-button" type="button">Chi tiết hậu kiểm</button></td>
    </tr>
  `).join("");

  studentTableSummary.textContent = `Hiển thị ${rows.length} mục trong số ${dashboardPayload.students.length} hồ sơ tiêu biểu`;
}

function toCsvCell(value) {
  const raw = value == null ? "" : String(value);
  return `"${raw.replace(/"/g, "\"\"")}"`;
}

function buildStudentsCsvRows(students) {
  const header = ["name", "email", "candidate_id", "room", "behaviors", "alerts", "risk"];
  const lines = [header.map((item) => toCsvCell(item)).join(",")];

  students.forEach((student) => {
    const row = [
      student.name,
      student.email,
      student.candidate_id,
      student.room,
      Array.isArray(student.behaviors) ? student.behaviors.join("; ") : "",
      student.alerts,
      student.risk,
    ];
    lines.push(row.map((item) => toCsvCell(item)).join(","));
  });

  return lines.join("\n");
}

function downloadStudentsCsv() {
  const students = Array.isArray(dashboardPayload.students) ? dashboardPayload.students : [];
  if (!students.length) {
    alert("Chua co du lieu thi sinh de xuat bao cao.");
    return;
  }

  const csvText = `\uFEFF${buildStudentsCsvRows(students)}`;
  const blob = new Blob([csvText], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const now = new Date();
  const filename = `students_report_${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}.csv`;

  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

riskChips.forEach((chip) => {
  chip.addEventListener("click", () => {
    riskChips.forEach((item) => item.classList.remove("is-selected"));
    chip.classList.add("is-selected");
    renderStudents(chip.dataset.risk);
  });
});

if (exportStudentsCsvButton) {
  exportStudentsCsvButton.addEventListener("click", downloadStudentsCsv);
}

const confidenceRange = document.getElementById("confidence-range");
const confidenceValue = document.getElementById("confidence-value");
const intervalRange = document.getElementById("interval-range");
const intervalValue = document.getElementById("interval-value");
const behaviorThresholdRange = document.getElementById("behavior-threshold-range");
const behaviorThresholdValue = document.getElementById("behavior-threshold-value");
const saveSettingsButton = document.getElementById("save-settings-button");
const resetSettingsButton = document.getElementById("reset-settings-button");
const settingsFeedback = document.getElementById("settings-feedback");

if (confidenceRange && confidenceValue) {
  confidenceRange.addEventListener("input", () => {
    confidenceValue.textContent = Number(confidenceRange.value).toFixed(2);
  });
}

if (intervalRange && intervalValue) {
  intervalRange.addEventListener("input", () => {
    intervalValue.textContent = Number(intervalRange.value).toFixed(1);
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
  };
}

function applyAiSettings(settings) {
  if (confidenceRange && confidenceValue && typeof settings.confidence_threshold === "number") {
    confidenceRange.value = settings.confidence_threshold.toFixed(2);
    confidenceValue.textContent = settings.confidence_threshold.toFixed(2);
  }
  if (intervalRange && intervalValue && typeof settings.extraction_interval_seconds === "number") {
    intervalRange.value = settings.extraction_interval_seconds.toFixed(1);
    intervalValue.textContent = settings.extraction_interval_seconds.toFixed(1);
  }
  if (behaviorThresholdRange && behaviorThresholdValue && typeof settings.behavior_threshold === "number") {
    behaviorThresholdRange.value = settings.behavior_threshold.toFixed(2);
    behaviorThresholdValue.textContent = settings.behavior_threshold.toFixed(2);
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
const stageFlagText = document.getElementById("review-stage-flag-text");
const incidentCards = Array.from(document.querySelectorAll(".incident-card[data-incident-time]"));
const incidentCountChip = document.getElementById("incident-count-chip");
const defaultStageFlagText = "Dang phat lai video hau kiem";
const emptyStageFlagText = "Chua co video de hau kiem";

function findActiveIncident(currentTime) {
  if (!incidentCards.length) {
    return null;
  }
  let activeCard = null;
  incidentCards.forEach((card) => {
    const marker = Number(card.dataset.incidentTime || 0);
    if (marker <= currentTime) {
      activeCard = card;
    }
  });
  return activeCard;
}

function setActiveIncident(currentTime) {
  const activeCard = findActiveIncident(currentTime);
  incidentCards.forEach((card) => card.classList.toggle("is-active", card === activeCard));
  if (!stageFlagText) {
    return;
  }
  if (activeCard) {
    stageFlagText.textContent = activeCard.dataset.incidentLabel || "Dang theo doi vi pham";
    return;
  }
  stageFlagText.textContent = reviewPayload.video_url ? defaultStageFlagText : emptyStageFlagText;
}

function bindIncidentCardNavigation() {
  incidentCards.forEach((card) => {
    card.addEventListener("click", () => {
      if (!reviewVideoPlayer) {
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
  if (incidentCountChip) {
    incidentCountChip.textContent = `${reviewPayload.incidents?.length || 0} su co`;
  }

  if (!incidentCards.length) {
    if (stageFlagText) {
      stageFlagText.textContent = reviewPayload.video_url ? defaultStageFlagText : emptyStageFlagText;
    }
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

activateTab(appShell?.dataset.initialTab || "overview");
renderStudents();
initializeReviewTimeline();
syncSelectedFileState();
