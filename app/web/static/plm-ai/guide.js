"use strict";
(() => {
  const root = "/static/plm-guide/";
  const allowedFiles = new Set(["index.html", "PLM_Guide.docx", "PLM_Guide.pdf", "plm-skill.zip"]);
  const status = document.getElementById("release-status");
  const reload = document.getElementById("reload");
  const links = [...document.querySelectorAll("a[data-asset]")];
  function disableLinks() {
    for (const link of links) {
      link.removeAttribute("href");
      link.setAttribute("aria-disabled", "true");
    }
    for (const element of document.querySelectorAll("[data-count]")) element.textContent = "—";
  }
  async function readJson(path) {
    const response = await fetch(path, { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) throw new Error("자료 응답 실패");
    return response.json();
  }
  async function loadRelease() {
    disableLinks();
    reload.disabled = true;
    status.textContent = "서버의 자료 목록을 확인하는 중입니다.";
    try {
      const catalog = await readJson(root + "current.json");
      const release = catalog.release_path;
      if (typeof release !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9-]{0,100}$/.test(release)) throw new Error("자료 판 경로 오류");
      const base = root + release + "/";
      const manifest = await readJson(base + "manifest.json");
      if (manifest.release !== release || !manifest.files || typeof manifest.files !== "object") throw new Error("자료 판 불일치");
      for (const link of links) {
        const file = link.dataset.asset;
        if (!allowedFiles.has(file) || !Object.prototype.hasOwnProperty.call(manifest.files, file)) continue;
        const fragment = link.dataset.fragment;
        link.setAttribute("href", base + file + (fragment ? "#" + encodeURIComponent(fragment) : ""));
        link.setAttribute("aria-disabled", "false");
      }
      for (const element of document.querySelectorAll("[data-count]")) {
        const count = manifest.counts?.[element.dataset.count];
        element.textContent = Number.isSafeInteger(count) && count >= 0 ? String(count) : "—";
      }
      status.textContent = "자료 판 " + release + (typeof manifest.created === "string" ? " · 작성 기준 " + manifest.created : "");
    } catch (_) {
      disableLinks();
      status.textContent = "서버 자료를 불러오지 못했습니다. 사내망과 서버 연결을 확인한 뒤 다시 불러오기를 눌러주세요.";
    } finally {
      reload.disabled = false;
    }
  }
  reload.addEventListener("click", loadRelease);
  for (const button of document.querySelectorAll("button[data-copy]")) {
    button.addEventListener("click", async () => {
      const text = document.getElementById(button.dataset.copy)?.textContent;
      const message = document.getElementById("copy-status");
      try {
        await navigator.clipboard.writeText(text);
        message.textContent = "질문을 복사했습니다. 새 대화에 붙여 넣으세요.";
      } catch (_) {
        message.textContent = "이 브라우저에서는 자동 복사를 사용할 수 없습니다. 질문 문장을 직접 선택해 복사하세요.";
      }
    });
  }
  loadRelease();
})();
