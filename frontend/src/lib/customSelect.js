// Progressively enhances a native <select> with a custom-styled dropdown.
// The native <select> stays in the DOM (hidden) as the source of truth, so
// every existing call site that reads/writes select.value or
// .selectedIndex, or listens for "change", keeps working untouched - this
// only swaps what the user sees and clicks.
const proto = HTMLSelectElement.prototype;
const valueDescriptor = Object.getOwnPropertyDescriptor(proto, "value");
const indexDescriptor = Object.getOwnPropertyDescriptor(proto, "selectedIndex");

function enhanceSelect(select) {
  if (select.dataset.enhanced) return;
  select.dataset.enhanced = "true";

  const extraClasses = [...select.classList].filter((c) => c !== "select");

  const wrapper = document.createElement("div");
  wrapper.className = ["custom-select", ...extraClasses].join(" ");
  wrapper.dataset.state = "closed";

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "custom-select-trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");

  const label = document.createElement("span");
  label.className = "custom-select-label truncate";
  trigger.appendChild(label);

  trigger.insertAdjacentHTML(
    "beforeend",
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="custom-select-chevron"><path d="M6 9l6 6 6-6"/></svg>`
  );

  const list = document.createElement("ul");
  list.className = "custom-select-list hidden";
  list.setAttribute("role", "listbox");
  list.tabIndex = -1;

  let optionEls = [];
  let highlighted = -1;

  function buildOptions() {
    list.innerHTML = "";
    optionEls = [...select.options].map((opt, i) => {
      const li = document.createElement("li");
      li.className = "custom-select-option";
      li.setAttribute("role", "option");
      li.textContent = opt.text;
      li.addEventListener("click", () => {
        selectIndex(i);
        close();
        trigger.focus();
      });
      list.appendChild(li);
      return li;
    });
  }

  function syncLabel() {
    const opt = select.options[select.selectedIndex];
    label.textContent = opt ? opt.text : "";
    optionEls.forEach((li, i) => li.setAttribute("aria-selected", String(i === select.selectedIndex)));
  }

  function selectIndex(i) {
    if (i < 0 || i >= select.options.length || i === select.selectedIndex) return;
    indexDescriptor.set.call(select, i);
    syncLabel();
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function highlight(i) {
    if (highlighted >= 0 && optionEls[highlighted]) optionEls[highlighted].classList.remove("is-highlighted");
    highlighted = i;
    if (optionEls[highlighted]) {
      optionEls[highlighted].classList.add("is-highlighted");
      optionEls[highlighted].scrollIntoView({ block: "nearest" });
    }
  }

  function onOutsideClick(e) {
    if (!wrapper.contains(e.target)) close();
  }

  function open() {
    if (select.disabled || wrapper.dataset.state === "open") return;
    syncLabel();
    highlight(select.selectedIndex);
    list.classList.remove("hidden");
    wrapper.dataset.state = "open";
    trigger.setAttribute("aria-expanded", "true");
    document.addEventListener("click", onOutsideClick, true);
  }

  function close() {
    if (wrapper.dataset.state === "closed") return;
    list.classList.add("hidden");
    wrapper.dataset.state = "closed";
    trigger.setAttribute("aria-expanded", "false");
    highlighted = -1;
    document.removeEventListener("click", onOutsideClick, true);
  }

  trigger.addEventListener("click", () => {
    wrapper.dataset.state === "open" ? close() : open();
  });

  let typeahead = "";
  let typeaheadTimer = null;

  trigger.addEventListener("keydown", (e) => {
    if (["ArrowDown", "ArrowUp", "Enter", " ", "Escape", "Home", "End", "Tab"].includes(e.key) && e.key !== "Tab") {
      e.preventDefault();
    }
    if (e.key === "ArrowDown") {
      open();
      highlight(Math.min((highlighted < 0 ? select.selectedIndex : highlighted) + 1, optionEls.length - 1));
    } else if (e.key === "ArrowUp") {
      open();
      highlight(Math.max((highlighted < 0 ? select.selectedIndex : highlighted) - 1, 0));
    } else if (e.key === "Home") {
      if (wrapper.dataset.state === "open") highlight(0);
    } else if (e.key === "End") {
      if (wrapper.dataset.state === "open") highlight(optionEls.length - 1);
    } else if (e.key === "Enter" || e.key === " ") {
      if (wrapper.dataset.state === "open") {
        if (highlighted >= 0) selectIndex(highlighted);
        close();
      } else {
        open();
      }
    } else if (e.key === "Escape") {
      close();
    } else if (e.key.length === 1 && e.key !== " ") {
      typeahead += e.key.toLowerCase();
      clearTimeout(typeaheadTimer);
      typeaheadTimer = setTimeout(() => (typeahead = ""), 500);
      const match = [...select.options].findIndex((o) => o.text.toLowerCase().startsWith(typeahead));
      if (match >= 0) {
        if (wrapper.dataset.state === "open") highlight(match);
        else selectIndex(match);
      }
    }
  });

  // Direct assignments elsewhere in the app (resetForm(), populating the
  // edit-event form, etc.) set select.value/.selectedIndex straight on the
  // native element without dispatching an event - intercept those so the
  // visible trigger label stays correct without those call sites needing
  // to know a custom widget is even there.
  Object.defineProperty(select, "value", {
    configurable: true,
    get() {
      return valueDescriptor.get.call(select);
    },
    set(v) {
      valueDescriptor.set.call(select, v);
      syncLabel();
    },
  });
  Object.defineProperty(select, "selectedIndex", {
    configurable: true,
    get() {
      return indexDescriptor.get.call(select);
    },
    set(i) {
      indexDescriptor.set.call(select, i);
      syncLabel();
    },
  });

  select.classList.add("hidden");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");

  select.parentNode.insertBefore(wrapper, select);
  wrapper.appendChild(trigger);
  wrapper.appendChild(list);
  wrapper.appendChild(select);

  buildOptions();
  syncLabel();
}

export function enhanceSelects(root = document) {
  root.querySelectorAll("select.select").forEach(enhanceSelect);
}
