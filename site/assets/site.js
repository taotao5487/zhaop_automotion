const state = {
  items: [],
  filteredItems: [],
  pendingUrl: "",
};

const searchInput = document.getElementById("searchInput");
const searchButton = document.getElementById("searchButton");
const resultMeta = document.getElementById("resultMeta");
const resultsList = document.getElementById("resultsList");
const emptyState = document.getElementById("emptyState");
const template = document.getElementById("jobCardTemplate");
const modal = document.getElementById("readOriginalModal");
const continueButton = document.getElementById("continueButton");
const cancelButton = document.getElementById("cancelButton");
const closeModalButton = document.getElementById("closeModalButton");

function normalizeKeyword(value) {
  return value.trim().toLowerCase();
}

function render(items) {
  resultsList.replaceChildren();

  if (!items.length) {
    emptyState.hidden = false;
    resultMeta.textContent = "没有找到匹配的招聘公告。";
    return;
  }

  emptyState.hidden = true;
  resultMeta.textContent = `共找到 ${items.length} 条招聘公告`;

  items.forEach((item) => {
    const fragment = template.content.cloneNode(true);
    fragment.querySelector(".job-title").textContent = item.title;
    fragment.querySelector(".job-date").textContent =
      `来源：${item.source_name || "未知来源"} · 发布日期：${item.publish_date}`;
    fragment.querySelector(".read-button").addEventListener("click", () => {
      state.pendingUrl = item.url;
      modal.hidden = false;
      document.body.style.overflow = "hidden";
    });
    resultsList.appendChild(fragment);
  });
}

function applyFilter() {
  const keyword = normalizeKeyword(searchInput.value);
  state.filteredItems = keyword
    ? state.items.filter((item) => {
        const title = item.title.toLowerCase();
        const sourceName = (item.source_name || "").toLowerCase();
        return title.includes(keyword) || sourceName.includes(keyword);
      })
    : [...state.items];
  render(state.filteredItems);
}

function closeModal() {
  modal.hidden = true;
  document.body.style.overflow = "";
}

async function loadItems() {
  resultMeta.textContent = "正在加载招聘公告...";
  const response = await fetch('./recruitment.json');
  const payload = await response.json();
  state.items = payload.items || [];
  state.filteredItems = [...state.items];
  render(state.filteredItems);
}

searchInput.addEventListener("input", applyFilter);
searchButton.addEventListener("click", applyFilter);
continueButton.addEventListener("click", () => {
  if (state.pendingUrl) {
    window.open(state.pendingUrl, "_blank", "noopener,noreferrer");
  }
  closeModal();
});
cancelButton.addEventListener("click", closeModal);
closeModalButton.addEventListener("click", closeModal);
modal.addEventListener("click", (event) => {
  if (event.target.dataset.closeModal === "true") {
    closeModal();
  }
});

loadItems().catch(() => {
  emptyState.hidden = false;
  resultMeta.textContent = "加载招聘公告失败，请重新生成站点数据。";
});
