// Shared by Events and the dashboard: fetches a Person and renders the
// detail bottom sheet (dates, notes, edit/delete-date icons, edit-card,
// add-date, delete-card). Viewing and deleting are handled entirely here;
// editing (add/edit a date, edit the card) hands off to whichever callback
// the including page provides, since the add/edit form and image cropper
// only exist on the Events page - the dashboard's callbacks navigate there
// instead of opening a form locally. Requires <PersonDetailModal /> to be
// present on the page.
import { marked } from "marked";
import DOMPurify from "dompurify";
import { escapeHtml, avatarHtml } from "./avatar.js";
import { formatDate, ageLabel, dueLabel, highlightNumber } from "./dates.js";
import { reminderLineHtml } from "./reminders.js";

marked.setOptions({ breaks: true });

export function createPersonDetail({ onEditCard, onEditEvent, onAddEvent, onDeleteEvent, onDeletePerson }) {
  const detailModal = document.getElementById("detail-modal");
  const detailAvatarWrap = document.getElementById("detail-avatar-wrap");
  const detailName = document.getElementById("detail-name");
  const detailEvents = document.getElementById("detail-events");

  let currentPerson = null;

  function render() {
    detailAvatarWrap.innerHTML = avatarHtml(currentPerson, "w-11 h-11 text-sm");
    detailName.textContent = currentPerson.name;

    detailEvents.innerHTML = "";
    const sorted = [...currentPerson.events].sort((a, b) => a.days_until - b.days_until);
    for (const ev of sorted) {
      const row = document.createElement("div");
      row.className = "p-3 rounded-lg bg-stone-50 dark:bg-white/5 [overflow-wrap:anywhere]";
      const notesHtml = ev.notes ? DOMPurify.sanitize(marked.parse(ev.notes)) : "";
      row.innerHTML = `
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="text-sm font-medium">${escapeHtml(ev.event_type.name)}</div>
            <div class="text-xs text-zinc-600 dark:text-zinc-300">
              ${escapeHtml(formatDate(ev))}${highlightNumber(ageLabel(ev))} &middot; ${dueLabel(ev.days_until)}
            </div>
            ${reminderLineHtml(ev)}
          </div>
          <div class="flex items-center gap-1 flex-shrink-0">
            <button type="button" class="btn-ghost p-1.5" data-edit-event="${ev.id}" aria-label="Edit">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
              </svg>
            </button>
            <button type="button" class="btn-ghost p-1.5" data-delete-event="${ev.id}" aria-label="Delete">
              <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          </div>
        </div>
        ${notesHtml ? `<div class="prose-notes text-xs text-zinc-600 dark:text-zinc-400 mt-2 pt-2 border-t">${notesHtml}</div>` : ""}
      `;
      detailEvents.appendChild(row);
    }
  }

  async function open(personId) {
    const res = await fetch(`/api/people/${personId}`);
    if (!res.ok) return null;
    currentPerson = await res.json();
    render();
    detailModal.classList.remove("hidden");
    return currentPerson;
  }

  function show() {
    if (currentPerson) detailModal.classList.remove("hidden");
  }

  function close() {
    detailModal.classList.add("hidden");
    currentPerson = null;
  }

  function setCurrent(person) {
    currentPerson = person;
    render();
  }

  detailModal.querySelectorAll(".detail-close").forEach((el) => el.addEventListener("click", close));
  detailModal.querySelector(".detail-backdrop").addEventListener("click", close);

  detailEvents.addEventListener("click", (e) => {
    const editBtn = e.target.closest("[data-edit-event]");
    const deleteBtn = e.target.closest("[data-delete-event]");
    if (editBtn) {
      const ev = currentPerson.events.find((e2) => e2.id === Number(editBtn.dataset.editEvent));
      const person = currentPerson;
      close();
      onEditEvent(person, ev);
    } else if (deleteBtn) {
      openConfirm("event", Number(deleteBtn.dataset.deleteEvent), "Delete this date?", "This cannot be undone.");
    }
  });

  document.getElementById("detail-add-event-btn").addEventListener("click", () => {
    const personId = currentPerson.id;
    close();
    onAddEvent(personId);
  });

  document.getElementById("detail-edit-card-btn").addEventListener("click", () => {
    const person = currentPerson;
    close();
    onEditCard(person);
  });

  document.getElementById("detail-delete-person-btn").addEventListener("click", () => {
    openConfirm("person", currentPerson.id, "Delete this card?", `This permanently deletes ${currentPerson.name} and all of their dates.`);
  });

  // ── Shared confirm modal ──
  const confirmModal = document.getElementById("confirm-modal");
  const confirmTitle = document.getElementById("confirm-title");
  const confirmMessage = document.getElementById("confirm-message");
  const confirmDeleteBtn = document.getElementById("confirm-delete-btn");
  let pendingDelete = null;

  function openConfirm(type, id, title, message) {
    pendingDelete = { type, id };
    confirmTitle.textContent = title;
    confirmMessage.textContent = message;
    confirmModal.classList.remove("hidden");
  }
  function closeConfirm() {
    confirmModal.classList.add("hidden");
    pendingDelete = null;
  }
  confirmModal.querySelectorAll(".confirm-cancel").forEach((el) => el.addEventListener("click", closeConfirm));
  confirmModal.querySelector(".confirm-backdrop").addEventListener("click", closeConfirm);

  confirmDeleteBtn.addEventListener("click", async () => {
    if (!pendingDelete) return;
    confirmDeleteBtn.disabled = true;
    try {
      if (pendingDelete.type === "event") {
        const res = await fetch(`/api/events/${pendingDelete.id}`, { method: "DELETE" });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || "Could not delete");
        }
        currentPerson = await res.json();
        render();
        await onDeleteEvent?.(currentPerson);
      } else if (pendingDelete.type === "person") {
        const res = await fetch(`/api/people/${pendingDelete.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Could not delete");
        const deletedId = pendingDelete.id;
        close();
        await onDeletePerson?.(deletedId);
      }
      closeConfirm();
    } catch (e) {
      closeConfirm();
      alert(e.message);
    } finally {
      confirmDeleteBtn.disabled = false;
    }
  });

  return {
    open,
    close,
    show,
    setCurrent,
    get current() {
      return currentPerson;
    },
  };
}
