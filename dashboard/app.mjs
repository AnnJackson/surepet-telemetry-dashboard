// Dashboard preferences live here so a fork can rename pets or change colors
// without changing the data pipeline.
const PET_COLORS = ["#5f9e8b", "#8d79a7", "#c48b67", "#668fc4"];
const PET_COLOR_OVERRIDES = { Pascal: "#5f9e8b", Joule: "#8d79a7" };
// Use UTC for the portable demo. A household fork can set an IANA zone such as
// "America/Phoenix" to match the local time used by its devices.
const DISPLAY_TIME_ZONE = "UTC";
const DAY_MS = 86_400_000;
const params = typeof location === "undefined" ? new URLSearchParams() : new URLSearchParams(location.search);
const dataUrl = params.get("data") || "../data/demo_telemetry.json";

export function feedingEvents(payload) {
  if (!payload || !Array.isArray(payload.events)) throw new Error("The data file does not contain an events array.");
  // The pipeline retains every numeric context, but labels known values as an
  // attribution. This consumption dashboard intentionally uses only the
  // pet-attributed food signal; owner additions and system events remain in
  // the normalized data for other uses.
  return payload.events
    .filter((event) => event?.category === "feeding" && event?.action === "food_change" && event?.attribution === "pet" && event?.subject?.name && Number.isFinite(Number(event.measurement?.value)))
    .map((event) => ({ ...event, amountGrams: Math.abs(Number(event.measurement.value)) }))
    .sort((a, b) => new Date(a.occurredAt) - new Date(b.occurredAt));
}

export function petsFor(events) {
  return [...new Map(events.map((event) => [event.subject.id || event.subject.name, event.subject.name])).entries()]
    .map(([id, name], index) => ({ id, name, color: PET_COLOR_OVERRIDES[name] || PET_COLORS[index % PET_COLORS.length] }));
}

function dateKey(value) {
  const date = value instanceof Date ? value : new Date(value);
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

function shiftDate(key, days) {
  const date = new Date(`${key}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return dateKey(date);
}

function minutesOfDay(value) {
  const date = new Date(value);
  return date.getUTCHours() * 60 + date.getUTCMinutes() + date.getUTCSeconds() / 60;
}

function formatDate(key, options = { month: "short", day: "numeric" }) {
  return new Intl.DateTimeFormat("en-US", { timeZone: DISPLAY_TIME_ZONE, ...options }).format(new Date(`${key}T12:00:00Z`));
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: DISPLAY_TIME_ZONE, timeZoneName: "short" }).format(new Date(value));
}

function formatClock(value) {
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: DISPLAY_TIME_ZONE }).format(new Date(value));
}

function timeAgo(from, to) {
  const minutes = Math.max(0, Math.round((new Date(to) - new Date(from)) / 60_000));
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  if (hours >= 24) return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  if (hours) return `${hours}h ${remainder}m`;
  return `${remainder}m`;
}

function eventsFor(events, pet, day) {
  return events.filter((event) => event.subject.id === pet.id && dateKey(event.occurredAt) === day);
}

function totalFor(events, pet, day) {
  return eventsFor(events, pet, day).reduce((sum, event) => sum + event.amountGrams, 0);
}

function weekStart(day) {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() - date.getUTCDay());
  return dateKey(date);
}

function completedWeeks(today) {
  const currentStart = weekStart(today);
  return Array.from({ length: 6 }, (_, index) => shiftDate(currentStart, (index - 6) * 7));
}

function weeklyAverages(events, pet, today) {
  return completedWeeks(today).map((start) => {
    const total = Array.from({ length: 7 }, (_, index) => totalFor(events, pet, shiftDate(start, index))).reduce((sum, value) => sum + value, 0);
    return { start, value: total / 7 };
  });
}

function setupCanvas(canvas, height) {
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(140, canvas.clientWidth);
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  context.lineCap = "round";
  context.lineJoin = "round";
  return { context, width, height };
}

function drawAxes(context, width, height, pad, maxY) {
  context.strokeStyle = "#343a3a";
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(pad.l, pad.t);
  context.lineTo(pad.l, height - pad.b);
  context.lineTo(width - pad.r, height - pad.b);
  context.stroke();
  context.fillStyle = "#8d9494";
  context.font = '700 9px "DM Sans", sans-serif';
  ["12 AM", "6 AM", "12 PM", "6 PM", "12 AM"].forEach((label, index) => {
    const x = pad.l + (width - pad.l - pad.r) * (index / 4);
    context.textAlign = index === 0 ? "left" : index === 4 ? "right" : "center";
    context.fillText(label, x, height - 4);
  });
  context.textAlign = "right";
  context.fillText(`${Math.round(maxY)} g`, pad.l - 4, pad.t + 3);
  context.fillText("0 g", pad.l - 4, height - pad.b + 3);
}

function drawPace(canvas, events, pet, today, maxY) {
  const { context, width, height } = setupCanvas(canvas, 356);
  const pad = { l: 26, r: 20, t: 12, b: 20 };
  const days = [-3, -2, -1, 0].map((offset) => shiftDate(today, offset));
  drawAxes(context, width, height, pad, maxY);
  days.map((day) => eventsFor(events, pet, day)).forEach((items, index) => {
    const isToday = index === 3;
    context.strokeStyle = pet.color;
    context.globalAlpha = isToday ? 1 : .18 + index * .12;
    context.lineWidth = isToday ? 3 : 1.7;
    let total = 0;
    let lastX = pad.l;
    let lastY = height - pad.b;
    context.beginPath();
    context.moveTo(pad.l, height - pad.b);
    items.forEach((event) => {
      const x = pad.l + (width - pad.l - pad.r) * (minutesOfDay(event.occurredAt) / 1440);
      const y0 = height - pad.b - (height - pad.t - pad.b) * (total / maxY);
      total += event.amountGrams;
      const y1 = height - pad.b - (height - pad.t - pad.b) * (total / maxY);
      context.lineTo(x, y0);
      context.lineTo(x, y1);
      lastX = x;
      lastY = y1;
    });
    context.stroke();
    if (items.length) {
      context.globalAlpha = 1;
      context.fillStyle = pet.color;
      context.font = '700 9px "DM Sans", sans-serif';
      context.textAlign = lastX > width - pad.r - 24 ? "right" : "left";
      context.fillText(`${Math.round(total)}g`, lastX > width - pad.r - 24 ? lastX - 4 : lastX + 4, Math.max(pad.t + 6, lastY - 4));
    }
  });
  context.globalAlpha = 1;
}

function drawFeedDots(canvas, events, pets, today) {
  const { context, width, height } = setupCanvas(canvas, Math.max(154, 46 + pets.length * 44));
  const pad = { l: 62, r: 7, t: 8, b: 22 };
  context.strokeStyle = "#252b2b";
  context.lineWidth = 1;
  [0, .25, .5, .75, 1].forEach((fraction) => {
    const x = pad.l + (width - pad.l - pad.r) * fraction;
    context.beginPath(); context.moveTo(x, pad.t); context.lineTo(x, height - pad.b); context.stroke();
  });
  context.font = '700 10px "DM Sans", sans-serif';
  pets.forEach((pet, row) => {
    const y = 27 + row * 44;
    context.fillStyle = "#b7bcbc"; context.textAlign = "right"; context.fillText(pet.name, pad.l - 9, y + 3);
    [-3, -2, -1, 0].forEach((offset) => {
      const isToday = offset === 0;
      eventsFor(events, pet, shiftDate(today, offset)).forEach((event) => {
        const x = pad.l + (width - pad.l - pad.r) * (minutesOfDay(event.occurredAt) / 1440);
        context.globalAlpha = isToday ? 1 : .24;
        context.fillStyle = pet.color;
        context.beginPath(); context.arc(x, y, isToday ? 6.5 : 2.5, 0, Math.PI * 2); context.fill();
      });
    });
  });
  context.globalAlpha = 1; context.fillStyle = "#8d9494"; context.font = '700 9px "DM Sans", sans-serif';
  ["12 AM", "6 AM", "12 PM", "6 PM", "12 AM"].forEach((label, index) => {
    const x = pad.l + (width - pad.l - pad.r) * (index / 4);
    context.textAlign = index === 0 ? "left" : index === 4 ? "right" : "center";
    context.fillText(label, x, height - 4);
  });
}

function drawDetail(canvas, events, pet, today) {
  const { context, width, height } = setupCanvas(canvas, 276);
  const pad = { l: 45, r: 32, t: 19, b: 8 };
  const days = Array.from({ length: 8 }, (_, index) => shiftDate(today, -index));
  const values = days.map((day) => totalFor(events, pet, day));
  const average = values.slice(1).reduce((sum, value) => sum + value, 0) / 7;
  const max = Math.max(20, average, ...values) * 1.18;
  const plotWidth = width - pad.l - pad.r;
  const rowHeight = (height - pad.t - pad.b) / 8;
  days.forEach((day, index) => {
    const y = pad.t + index * rowHeight + 4;
    const barHeight = rowHeight - 8;
    context.fillStyle = pet.color; context.globalAlpha = index === 0 ? 1 : .72;
    context.fillRect(pad.l, y, plotWidth * (values[index] / max), barHeight);
    context.globalAlpha = 1; context.fillStyle = "#9ba2a2"; context.font = '700 9px "DM Sans", sans-serif'; context.textAlign = "right";
    context.fillText(index === 0 ? "Today" : formatDate(day), pad.l - 7, y + barHeight / 2 + 3);
    context.fillStyle = pet.color; context.textAlign = "left";
    context.fillText(`${Math.round(values[index])}g`, Math.min(width - pad.r - 22, pad.l + plotWidth * (values[index] / max) + 5), y + barHeight / 2 + 3);
  });
  const averageX = pad.l + plotWidth * (average / max);
  context.strokeStyle = "#d6dada"; context.globalAlpha = .55; context.setLineDash([3, 4]);
  context.beginPath(); context.moveTo(averageX, pad.t); context.lineTo(averageX, height - pad.b); context.stroke(); context.setLineDash([]); context.globalAlpha = 1;
  context.fillStyle = "#d6dada"; context.font = '700 9px "DM Sans", sans-serif'; context.textAlign = "center";
  context.fillText(`AVG ${Math.round(average)}g`, Math.min(width - 28, Math.max(35, averageX)), 9);
}

function drawWeeklyTrend(canvas, events, pet, today, maxY) {
  const { context, width, height } = setupCanvas(canvas, 230);
  const pad = { l: 6, r: 6, t: 24, b: 34 };
  const weeks = weeklyAverages(events, pet, today);
  const plotWidth = width - pad.l - pad.r;
  const plotHeight = height - pad.t - pad.b;
  context.strokeStyle = "#343a3a"; context.lineWidth = 1;
  context.beginPath(); context.moveTo(pad.l, height - pad.b); context.lineTo(width - pad.r, height - pad.b); context.stroke();
  const slot = plotWidth / weeks.length;
  const barWidth = Math.min(34, slot * .58);
  weeks.forEach((week, index) => {
    const barHeight = plotHeight * (week.value / maxY);
    const x = pad.l + slot * index + (slot - barWidth) / 2;
    const y = height - pad.b - barHeight;
    context.fillStyle = pet.color; context.globalAlpha = .86; context.fillRect(x, y, barWidth, barHeight); context.globalAlpha = 1;
    context.font = '800 9px "DM Sans", sans-serif'; context.textAlign = "center";
    context.fillText(`${Math.round(week.value)}g`, x + barWidth / 2, Math.max(10, y - 6));
    context.fillStyle = "#9ba2a2"; context.font = '700 9px "DM Sans", sans-serif';
    context.fillText(formatDate(week.start, { month: "short", day: "numeric" }), x + barWidth / 2, height - 8);
  });
}

function addFigure(container, pet, className, label) {
  const figure = document.createElement("figure");
  const caption = document.createElement("figcaption");
  const dot = document.createElement("span"); dot.className = "pet-dot"; dot.style.setProperty("--pet-color", pet.color);
  caption.append(dot, document.createTextNode(pet.name));
  const canvas = document.createElement("canvas"); canvas.className = className; canvas.setAttribute("role", "img"); canvas.setAttribute("aria-label", `${pet.name} ${label}`);
  figure.append(caption, canvas); container.append(figure);
  return canvas;
}

function buildChartContainers(pets) {
  const pace = document.querySelector("#pace-charts");
  const detail = document.querySelector("#detail-charts");
  const weekly = document.querySelector("#weekly-charts");
  pace.replaceChildren(); detail.replaceChildren(); weekly.replaceChildren();
  return new Map(pets.map((pet) => [pet.id, {
    pace: addFigure(pace, pet, "pace-chart", "cumulative food consumption by time of day"),
    detail: addFigure(detail, pet, "detail-chart", "daily food consumption for today and the seven prior days"),
    weekly: addFigure(weekly, pet, "weekly-chart", "average daily food consumption for six completed weeks"),
  }]));
}

function renderLatest(events, pet, now) {
  const card = document.createElement("article");
  card.className = "latest-card"; card.style.setProperty("--pet-color", pet.color);
  const last = [...events].reverse().find((event) => event.subject.id === pet.id);
  const name = document.createElement("p"); name.className = "latest-name"; name.textContent = `${pet.name}'s last food`;
  const elapsed = document.createElement("p"); elapsed.className = "latest-time";
  const detail = document.createElement("p"); detail.className = "latest-detail";
  if (!last) { elapsed.textContent = "No data"; card.append(name, elapsed); return card; }
  elapsed.textContent = timeAgo(last.occurredAt, now);
  const amount = document.createElement("strong"); amount.textContent = `${Math.round(last.amountGrams)}g`;
  detail.append(amount, document.createTextNode(` · ${formatClock(last.occurredAt)}`), document.createElement("br"), document.createTextNode(formatDate(dateKey(last.occurredAt), { weekday: "short", month: "short", day: "numeric" })));
  card.append(name, elapsed, detail);
  return card;
}

function renderLatestGrid(events, pets, now) {
  document.querySelector("#latest-grid").replaceChildren(...pets.map((pet) => renderLatest(events, pet, now)));
}

function renderTotals(events, pets, today) {
  const values = pets.map((pet) => {
    const devices = new Map();
    eventsFor(events, pet, today).forEach((event) => {
      const name = event.device?.name || event.device?.id || "Unknown feeder";
      devices.set(name, (devices.get(name) || 0) + event.amountGrams);
    });
    const segments = [...devices].map(([name, value]) => ({ name, value })).sort((a, b) => a.name.localeCompare(b.name));
    return { pet, segments, value: segments.reduce((sum, segment) => sum + segment.value, 0) };
  });
  const max = Math.max(1, ...values.map((item) => item.value));
  document.querySelector("#today-label").textContent = formatDate(today, { weekday: "short", month: "short", day: "numeric" });
  const container = document.querySelector("#total-bars"); container.replaceChildren();
  values.forEach(({ pet, segments, value }) => {
    const row = document.createElement("div"); row.className = "total-row";
    const name = document.createElement("span"); name.className = "pet"; name.textContent = pet.name;
    const main = document.createElement("div"); main.className = "total-main";
    const track = document.createElement("div"); track.className = "bar-track";
    const stack = document.createElement("div"); stack.className = "bar-stack"; stack.style.width = `${Math.max(2, value / max * 100)}%`;
    segments.forEach((segment) => { const part = document.createElement("div"); part.className = "bar-segment"; part.style.flexBasis = `${value ? segment.value / value * 100 : 0}%`; part.style.background = pet.color; const amount = document.createElement("strong"); amount.textContent = `${Math.round(segment.value)}g`; part.append(amount); stack.append(part); });
    track.append(stack);
    const breakdown = document.createElement("div"); breakdown.className = "device-breakdown";
    segments.forEach((segment) => { const item = document.createElement("span"); item.textContent = `${segment.name} `; const amount = document.createElement("strong"); amount.textContent = `${Math.round(segment.value)}g`; item.append(amount); breakdown.append(item); });
    main.append(track, breakdown);
    const total = document.createElement("span"); total.className = "total-value"; total.style.color = pet.color; total.textContent = `${Math.round(value)}g`;
    row.append(name, main, total); container.append(row);
  });
}

export function renderDashboard(payload) {
  const events = feedingEvents(payload);
  if (!events.length) throw new Error("No pet-associated food events were found.");
  const pets = petsFor(events);
  const today = dateKey(events[events.length - 1].occurredAt);
  const asOf = payload.generatedAt || events[events.length - 1].occurredAt;
  const charts = buildChartContainers(pets);
  renderLatestGrid(events, pets, Date.now());
  renderTotals(events, pets, today);
  const totals = pets.flatMap((pet) => [-3, -2, -1, 0].map((offset) => totalFor(events, pet, shiftDate(today, offset))));
  const paceMax = Math.max(10, Math.ceil(Math.max(...totals) / 10) * 10);
  const weeklyMax = Math.max(10, Math.ceil(Math.max(...pets.flatMap((pet) => weeklyAverages(events, pet, today).map((week) => week.value))) / 10) * 10);
  pets.forEach((pet) => { const elements = charts.get(pet.id); drawPace(elements.pace, events, pet, today, paceMax); drawDetail(elements.detail, events, pet, today); drawWeeklyTrend(elements.weekly, events, pet, today, weeklyMax); });
  drawFeedDots(document.querySelector("#feed-dots"), events, pets, today);
  document.querySelector("#retrieved-at").textContent = formatDateTime(asOf);
  const hoursOld = (Date.now() - new Date(asOf)) / 3_600_000;
  const freshness = document.querySelector("#freshness"); freshness.classList.toggle("stale", hoursOld > 2.5); freshness.lastElementChild.textContent = hoursOld > 2.5 ? "Snapshot data" : hoursOld > 1.5 ? "Refresh due" : "Data current";
}

async function start() {
  const response = await fetch(dataUrl, { cache: "no-store" });
  if (!response.ok) throw new Error(`Could not load telemetry data (${response.status}).`);
  const payload = await response.json();
  renderDashboard(payload);
  window.addEventListener("resize", (() => { let timer; return () => { clearTimeout(timer); timer = setTimeout(() => renderDashboard(payload), 120); }; })());
  window.setInterval(() => renderLatestGrid(feedingEvents(payload), petsFor(feedingEvents(payload)), Date.now()), 30_000);
}

if (typeof document !== "undefined") {
  start().catch((error) => { document.querySelector("main").innerHTML = `<div class="error"><strong>Dashboard unavailable</strong><p>${error.message}</p></div>`; });
}
