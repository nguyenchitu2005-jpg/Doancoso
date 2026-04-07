const tabs = document.querySelectorAll(".nav-item");
const sections = document.querySelectorAll(".page-section");
const pageTitle = document.getElementById("page-title");
const dashboardPayload = JSON.parse(document.getElementById("dashboard-data").textContent);
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

riskChips.forEach((chip) => {
  chip.addEventListener("click", () => {
    riskChips.forEach((item) => item.classList.remove("is-selected"));
    chip.classList.add("is-selected");
    renderStudents(chip.dataset.risk);
  });
});

const confidenceRange = document.getElementById("confidence-range");
const confidenceValue = document.getElementById("confidence-value");
const intervalRange = document.getElementById("interval-range");
const intervalValue = document.getElementById("interval-value");

confidenceRange.addEventListener("input", () => {
  confidenceValue.textContent = Number(confidenceRange.value).toFixed(2);
});

intervalRange.addEventListener("input", () => {
  intervalValue.textContent = Number(intervalRange.value).toFixed(1);
});

const uploadTriggers = document.querySelectorAll(".upload-trigger");
const uploadFileInput = document.getElementById("review-video-file");
const selectedFileName = document.getElementById("selected-file-name");

uploadTriggers.forEach((trigger) => {
  trigger.addEventListener("click", () => {
    const fileInputId = trigger.dataset.fileTrigger;
    const input = document.getElementById(fileInputId);
    if (input) {
      input.click();
    }
  });
});

if (uploadFileInput && selectedFileName) {
  uploadFileInput.addEventListener("change", () => {
    const [file] = uploadFileInput.files;
    selectedFileName.textContent = file ? `Đã chọn: ${file.name}` : "Chưa chọn tệp nào";
  });
}

activateTab(appShell?.dataset.initialTab || "overview");
renderStudents();
