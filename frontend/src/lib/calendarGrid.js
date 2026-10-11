// The month grid shared by /calendar and the public /shared/[token] page.
// Perpetual on purpose: navigation is month-only, and which dates land on a
// day comes from each date's type cadence (occursOn in lib/dates.js).
import { avatarHtml } from "./avatar.js";
import { MONTH_NAMES, occursOn } from "./dates.js";

function daysInMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

// 0 = Monday .. 6 = Sunday, matching the Mon-Sun header row.
function mondayIndex(year, month, day) {
  const jsDay = new Date(year, month - 1, day).getDay();
  return (jsDay + 6) % 7;
}

// Wires the prev/next/today buttons and renders into `grid`/`monthLabel`.
// `onOpenDay(month, day, year, entries)` fires when a populated day is clicked.
export function createMonthGrid({ people, grid, monthLabel, prevBtn, nextBtn, todayBtn, onOpenDay }) {
  const allEntries = people.flatMap((person) => person.events.map((event) => ({ person, event })));
  const today = new Date();
  let viewYear = today.getFullYear();
  let viewMonth = today.getMonth() + 1; // 1-12

  function render() {
    monthLabel.textContent = `${MONTH_NAMES[viewMonth - 1]} ${viewYear}`;
    grid.innerHTML = "";

    const daysThisMonth = daysInMonth(viewYear, viewMonth);
    const leading = mondayIndex(viewYear, viewMonth, 1);

    let prevMonth = viewMonth - 1;
    let prevYear = viewYear;
    if (prevMonth < 1) {
      prevMonth = 12;
      prevYear -= 1;
    }
    const daysPrevMonth = daysInMonth(prevYear, prevMonth);

    let nextMonth = viewMonth + 1;
    let nextYear = viewYear;
    if (nextMonth > 12) {
      nextMonth = 1;
      nextYear += 1;
    }

    const totalCells = Math.ceil((leading + daysThisMonth) / 7) * 7;

    for (let i = 0; i < totalCells; i++) {
      let cellMonth = viewMonth;
      let cellYear = viewYear;
      let cellDay;
      let outside = false;

      if (i < leading) {
        cellMonth = prevMonth;
        cellYear = prevYear;
        cellDay = daysPrevMonth - leading + i + 1;
        outside = true;
      } else if (i >= leading + daysThisMonth) {
        cellMonth = nextMonth;
        cellYear = nextYear;
        cellDay = i - leading - daysThisMonth + 1;
        outside = true;
      } else {
        cellDay = i - leading + 1;
      }

      const isToday =
        !outside &&
        viewYear === today.getFullYear() &&
        viewMonth === today.getMonth() + 1 &&
        cellDay === today.getDate();
      // Skip events that haven't happened yet as of this cell's year -
      // a birthday shouldn't appear on the calendar before it occurred.
      const entries = allEntries.filter(({ event }) => occursOn(event, cellYear, cellMonth, cellDay));

      const cell = document.createElement(entries.length ? "button" : "div");
      if (entries.length) cell.type = "button";
      cell.className = [
        "aspect-square rounded-lg p-1 sm:p-1.5 flex flex-col items-start gap-0.5 text-left border transition-colors",
        outside ? "opacity-40" : "",
        isToday ? "border-accent dark:border-accent-light bg-accent/10 dark:bg-accent-light/10" : "border-transparent",
        entries.length ? "hover:bg-stone-50 dark:hover:bg-white/5 cursor-pointer" : "cursor-default",
      ]
        .filter(Boolean)
        .join(" ");

      const dayNumClass = isToday
        ? "font-semibold text-accent dark:text-accent-light"
        : "text-zinc-600 dark:text-zinc-300";
      let html = `<span class="text-xs sm:text-sm ${dayNumClass}">${cellDay}</span>`;

      if (entries.length === 1) {
        html += `<div class="mt-1 sm:mt-1.5">${avatarHtml(entries[0].person, "w-4 h-4 sm:w-5 sm:h-5 text-[8px]")}</div>`;
      } else if (entries.length > 1) {
        html += `<div class="mt-1 sm:mt-1.5 grid grid-cols-2 gap-0.5 w-full max-w-[34px] sm:max-w-[42px] items-center">
          ${avatarHtml(entries[0].person, "w-full aspect-square min-w-0 text-[8px]")}
          <div class="w-full aspect-square min-w-0 rounded-full bg-zinc-200 dark:bg-zinc-700 flex items-center justify-center">
            <span class="text-[7px] sm:text-[8px] font-medium text-zinc-700 dark:text-zinc-200">+${entries.length - 1}</span>
          </div>
        </div>`;
      }

      cell.innerHTML = html;
      if (entries.length) {
        cell.setAttribute("aria-label", `${MONTH_NAMES[cellMonth - 1]} ${cellDay}: ${entries.length} ${entries.length === 1 ? "event" : "events"}`);
        cell.addEventListener("click", () => onOpenDay(cellMonth, cellDay, cellYear, entries));
      }
      grid.appendChild(cell);
    }
  }

  prevBtn.addEventListener("click", () => {
    viewMonth -= 1;
    if (viewMonth < 1) {
      viewMonth = 12;
      viewYear -= 1;
    }
    render();
  });
  nextBtn.addEventListener("click", () => {
    viewMonth += 1;
    if (viewMonth > 12) {
      viewMonth = 1;
      viewYear += 1;
    }
    render();
  });
  todayBtn.addEventListener("click", () => {
    viewYear = today.getFullYear();
    viewMonth = today.getMonth() + 1;
    render();
  });

  return { render, today };
}
