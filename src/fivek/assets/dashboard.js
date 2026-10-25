const TOKEN = document.querySelector('meta[name="fivek-token"]').content;
const BASE_PATH = document.querySelector('meta[name="fivek-base-path"]').content;
const appUrl = (path) => BASE_PATH + path.replace(/^\/+/, '');
let reportData = null,
  shown = 30,
  weekOffset = 0,
  lastBusy = false,
  lastGenerated = null,
  reportEtag = null,
  chosenDay = null,
  polling = false;
const $ = (id) => document.getElementById(id);
async function refreshCalendarStatus() {
  try {
    const value = await get('/api/calendar/status');
    $('calendar-status').textContent = value.connected
      ? 'Apple Calendar connected · ' + value.calendar + (value.last_sync?.synced_at ? ' · last sync ' + new Date(value.last_sync.synced_at).toLocaleString() : '')
      : 'Apple Calendar is not connected yet.';
    $('calendar-sync').disabled = !value.connected;
    $('automation-status').textContent = value.automation?.installed
      ? (value.automation.platform === 'linux' ? 'Automatic refresh runs on this server every 2 hours.' : 'Automatic refresh: every 2 hours and at Windows sign-in. This PC must be awake and your account signed in.') +
        (value.last_cycle ? ' Last cycle: ' + value.last_cycle.status.replaceAll('_',' ') + ' · ' + value.last_cycle.message : '')
      : 'Automatic refresh has not been installed.';
    if (value.last_error) $('calendar-status').textContent += ' · ' + value.last_error.message;
  } catch (_) { $('calendar-status').textContent = 'Calendar status could not be read.'; }
}
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const time = (s) =>
  s == null
    ? "—"
    : Math.floor(Math.round(s) / 60) +
      ":" +
      String(Math.round(s) % 60).padStart(2, "0");
const durationTime = (seconds) => {
  if (seconds == null) return "—";
  const whole = Math.round(seconds);
  return whole < 3600
    ? time(whole)
    : Math.floor(whole / 3600) +
        ":" +
        String(Math.floor(whole / 60) % 60).padStart(2, "0") +
        ":" +
        String(whole % 60).padStart(2, "0");
};
async function get(path) {
  const r = await fetch(appUrl(path));
  if (!r.ok) throw Error("Could not load local data");
  return r.json();
}
async function post(path, body) {
  const r = await fetch(appUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-FiveK-Token": TOKEN },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw Error(data.error || "Request failed");
  return data;
}
function status(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}
function chart(items, key, label) {
  const w = 500,
    h = 170,
    base = 135,
    max = Math.max(1, ...items.map((x) => x[key] ?? 0));
  if (!items.length || items.every((x) => x[key] == null))
    return '<p class="empty">No recorded ' + esc(label) + " yet.</p>";
  const step = 420 / items.length,
    barWidth = Math.max(1, step * 0.72),
    dateStride = Math.ceil(items.length / 4);
  return (
    '<svg viewBox="0 0 ' +
    w +
    " " +
    h +
    '" role="img" aria-label="' +
    esc(label) +
    '"><line x1="30" y1="135" x2="480" y2="135" stroke="#d7ded3"/>' +
    '<text x="42" y="44" text-anchor="end" font-size="17" fill="#61736b">' + esc(Math.round(max * 10) / 10) + '</text>' +
    '<text x="42" y="140" text-anchor="end" font-size="17" fill="#61736b">0</text>' +
    items
      .map((x, i) => {
        const value = x[key],
          bh = ((value ?? 0) / max) * 95;
        return (
          "<g><title>" +
          esc(x.start || x.date) +
          ": " +
          esc(value == null ? "missing" : value) +
          '</title><rect x="' +
          (52 + i * step) +
          '" y="' +
          (base - bh) +
          '" width="' +
          barWidth +
          '" height="' +
          bh +
          '" rx="3" fill="' +
          (i === items.length - 1 ? "#245c45" : "#b8cb8b") +
          '"/>' +
          ((i % dateStride === 0 && i <= items.length - 1 - dateStride) || i === items.length - 1
            ? '<text x="' +
              (52 + i * step + barWidth / 2) +
              '" y="161" font-size="18" text-anchor="' + (i === items.length - 1 ? 'end' : i === 0 ? 'start' : 'middle') + '" fill="#61736b">' +
              esc((x.start || x.date).slice(5)) +
              "</text>"
            : "") +
          (items.length <= 8
            ? '<text x="' +
              (52 + i * step + barWidth / 2) +
              '" y="' +
              (base - bh - 5) +
              '" font-size="17" text-anchor="middle" fill="#61736b">' +
              esc(value == null ? "·" : Math.round(value * 10) / 10) +
              "</text>"
            : "") +
          "</g>"
        );
      })
      .join("") +
    "</svg>"
  );
}
function choose(index) {
  const s = reportData.plan.sessions[index];
  if (!s) return;
  chosenDay = s.date;
  $("session").innerHTML =
    "<h3>" +
    esc(s.weekday + " · " + s.title) +
    "</h3><p>" +
    esc(s.details) +
    "</p>" +
    (s.readiness_note
      ? '<p class="session-caveat">' + esc(s.readiness_note) + "</p>"
      : "") +
    (s.next_action
      ? '<p class="recorded-note">' + esc(s.next_action) + "</p>"
      : "") +
    (s.steps?.length
      ? '<ol class="workout-steps">' +
        s.steps
          .map(
            (step) =>
              "<li><strong>" +
              esc(step.minutes) +
              " min</strong> · " +
              esc(step.kind.replaceAll("_", " ")) +
              " · " +
              esc(step.effort) +
              "</li>",
          )
          .join("") +
        "</ol>"
      : "") +
    (s.decision_reasons?.length
      ? '<details class="session-reasons"><summary>Why this session?</summary><ul>' +
        s.decision_reasons
          .map((reason) => "<li>" + esc(reason) + "</li>")
          .join("") +
        "</ul></details>"
      : "") +
    '<span class="badge">' +
    esc(
      s.status === "recorded"
        ? "Recorded today"
        : s.status === "skipped"
          ? "Skipped"
          : s.effort,
    ) +
    (s.optional ? " · optional" : "") +
    "</span>";
  document.querySelectorAll(".day").forEach((el) => {
    el.classList.toggle("active", Number(el.dataset.index) === index);
    el.setAttribute("aria-pressed", String(Number(el.dataset.index) === index));
  });
}
function renderActivities() {
  const items = reportData.metrics.activities.filter(
    (a) =>
      ($("filter").value === "all" || a.kind === $("filter").value) &&
      (a.name + " " + a.date + " " + a.sport)
        .toLowerCase()
        .includes($("activity-search").value.toLowerCase()),
  );
  $("activities").innerHTML =
    items
      .slice(0, shown)
      .map(
        (a) =>
          "<tr><td>" +
          esc(a.date) +
          '</td><td><button class="activity-link" data-activity="' +
          esc(a.id) +
          '">' +
          esc(a.name) +
          "</button>" +
          '<br><span class="evidence">' +
          esc(a.sport) +
          "</span></td><td>" +
          esc(a.km == null ? "—" : a.km.toFixed(1) + " km") +
          "</td><td>" +
          esc(a.minutes == null ? "—" : a.minutes.toFixed(0) + " min") +
          "</td><td>" +
          time(a.pace_seconds_km) +
          "</td><td>" +
          esc(a.average_hr == null ? "—" : a.average_hr.toFixed(0)) +
          "</td></tr>",
      )
      .join("") ||
    '<tr><td colspan="6" class="empty">No activities yet. Refresh Garmin to import your history.</td></tr>';
  $("more").classList.toggle("hidden", items.length <= shown);
}
function render(r) {
  reportData = r;
  const m = r.metrics,
    p = r.plan;
  setupChoices(r);
  const history = m.benchmark_history || [];
  $("benchmark-history").innerHTML = history.length
    ? '<div class="result-history"><strong>Your measured results</strong>' +
      history
        .slice(-5)
        .reverse()
        .map(
          (value) =>
            "<div>" +
            esc(value.date) +
            " · " +
            time(value.seconds) +
            " · " +
            esc(value.kind.replaceAll("_", " ")) +
            "</div>",
        )
        .join("") +
      "</div>"
    : "";
  $("metric-coverage").innerHTML = m.data_quality
    ? "<h3>Usable recovery coverage</h3>" +
      Object.entries(m.data_quality.metrics)
        .map(
          ([name, value]) =>
            '<p class="evidence">' +
            esc(name.replaceAll("_", " ")) +
            ": " +
            value.recent_7_days +
            " readings in the last 7 days · latest " +
            esc(value.latest_date || "none") +
            "</p>",
        )
        .join("")
    : "";

  $("today-title").textContent =
    m.recovery.status === "pause" && p.sessions.length
      ? "Pause impact training"
      : p.sessions[0]?.status === "recorded"
        ? "Your activity is recorded"
        : p.sessions[0]?.status === "skipped"
          ? "Match skipped · recover"
          : p.sessions[0]?.title || "Set your next 5K goal";
  $("today-summary").textContent =
    p.sessions[0]?.next_action || p.sessions[0]?.details || p.strategy;
  $("best-basis").textContent =
    m.benchmark?.origin === "athlete"
      ? "Your confirmed " + m.benchmark.kind.replaceAll("_", " ")
      : "Observed outdoor effort · not a prediction";
  $("countdown").textContent = Math.max(0, p.days_remaining) + " days";
  document.querySelector(".goal .eyebrow").textContent =
    p.target_date + " · 5 kilometres";
  $("reference").textContent = p.target_time
    ? (m.benchmark?.origin === "athlete"
        ? "Your confirmed result: "
        : "Your observed reference: ") +
      p.target_time.reference +
      ". Build from here."
    : "Your first step: establish an observed 5K reference.";
  $("level").textContent = p.level + " running plan";
  $("distance").textContent = m.baseline_weekly_km.toFixed(1) + " km/wk";
  $("run-count").textContent = m.recent_run_count + " runs across " + (m.recent_running_days ?? 'unknown') + " dates in the last 28 days";
  $("best").textContent = m.benchmark
    ? time(m.benchmark.seconds)
    : "No reference";
  $("sleep").textContent =
    m.sleep_average_hours == null
      ? "No readings"
      : m.sleep_average_hours.toFixed(1) + " h";
  $("sleep-count").textContent = m.data_quality
    ? m.data_quality.metrics.sleep_hours.recent_7_days + " of 7 nights recorded"
    : "Recorded nights only";
  $("recovery").textContent = {
    normal: "No flags",
    unknown: "Unknown",
    reduce: "Take it easy",
    pause: "Pause impact",
  }[m.recovery.status];
  const quality = m.data_quality;
  $("freshness").textContent = quality
    ? "Latest activity: " +
      (quality.latest_activity_date || "none") +
      " · usable recovery: " +
      (quality.latest_recovery_date || "none") +
      " · last import: " +
      (r.sync.run?.finished_at
        ? new Date(r.sync.run.finished_at).toLocaleString()
        : r.sync.run?.status || "not started")
    : "";
  $("recovery-note").textContent = m.recovery.flags.length
    ? m.recovery.flags.length + " signals to reassess"
    : "Wearable data + your check-in";
  renderWeek(p);
  $("pace").textContent = p.pace_guidance;
  const progression = p.progression;
  $("progression-review").innerHTML = progression
    ? '<h3>Your next step · ' + esc(progression.title) + '</h3><p>' + esc(progression.action) +
      '</p><details><summary>Milestones and current evidence</summary>' +
      progression.milestones.map(item => '<article class="insight"><h4>' + esc(item.title) +
        ' · ' + esc(item.status) + '</h4><p>' + esc(item.detail) + '</p><p class="evidence">' +
        esc(item.evidence) + '</p></article>').join('') +
      '<p class="evidence">Taper starts ' + esc(progression.taper_start) + '. ' + esc(progression.note) + '</p></details>'
    : '';
  $("insights").innerHTML = m.insights
    .map(
      (x) =>
        '<article class="insight"><h3>' +
        esc(x.title) +
        "</h3><p>" +
        esc(x.detail) +
        '</p><div class="evidence">' +
        esc(x.evidence) +
        "</div></article>",
    )
    .join("");
  renderHistory(r);
  renderRecovery(r);
  $("strategy").textContent = p.strategy;
  $("plan").innerHTML = p.weeks
    .map(
      (w) =>
        "<tr><td>" +
        esc(w.start) +
        "</td><td>" +
        esc(w.phase) +
        (w.conditional ? ' <span class="badge">conditional</span>' : "") +
        "</td><td>" +
        esc(
          [
            w.planned_training_km ? w.planned_training_km + " km training" : "",
            w.planned_training_minutes
              ? w.planned_training_minutes + " min easy/run-walk"
              : "",
            w.goal_run_km ? w.goal_run_km + " km goal effort" : "",
          ]
            .filter(Boolean)
            .join(" + ") || "No remaining running",
        ) +
        "</td></tr>",
    )
    .join("");
  $("rules").innerHTML = (p.rules || [])
    .map((x) => "<p>" + esc(x) + "</p>")
    .join("");
  $("limits").innerHTML =
    '<h3 style="margin-top:20px">Assumptions</h3>' +
    r.limitations.map((x) => '<p class="evidence">' + esc(x) + "</p>").join("");
  $("sources").innerHTML = r.sources
    .map(
      (s) =>
        '<p><a href="' +
        esc(s.url) +
        '" target="_blank" rel="noreferrer">' +
        esc(s.title) +
        "</a></p>",
    )
    .join("");
  $("coverage").innerHTML =
    (r.sync.scope
      ? "<h3>Current import scope</h3><p>" +
        esc(r.sync.scope.history_start + " → " + r.sync.scope.history_end) +
        " · " +
        r.sync.scope.expected +
        ' expected resources</p><p class="evidence">' +
        r.sync.scope.ok +
        " non-empty responses · " +
        r.sync.scope.empty +
        " empty · " +
        r.sync.scope.unavailable +
        " unavailable · " +
        r.sync.scope.error +
        " failed · " +
        r.sync.scope.missing +
        ' missing</p><p class="evidence">' +
        esc(r.sync.scope.note) +
        "</p>"
      : "") +
    '<p style="margin-top:20px">Refresh: ' +
    esc(r.sync.run?.status || "not started") +
    " · " +
    esc(JSON.stringify(r.sync.counts)) +
    "</p>" +
    r.sync.coverage
      .map(
        (x) =>
          '<p class="evidence">' +
          esc(x.stream) +
          ": " +
          x.records +
          " responses · " +
          esc(x.first_key) +
          " → " +
          esc(x.last_key) +
          "</p>",
      )
      .join("") +
    r.sync.issues
      .map(
        (x) =>
          '<p class="evidence">' +
          esc(x.stream + " " + x.key + " · " + x.status + ": " + x.message) +
          "</p>",
      )
      .join("");
  $("updated").textContent =
    "Report generated " + new Date(r.generated_at).toLocaleString();
  renderActivities();
  renderCheckinHistory();
  renderFootballReview();
  renderOverview(r);
  $("date").max = r.as_of;
  if (!$("date").value) {
    $("date").value = r.as_of;
    fillCheckin();
  }
}
function renderFootballReview() {
  const review = reportData.metrics.football_summary;
  if (!review) return;
  $("football-review").innerHTML =
    '<p><strong>' + esc(review.known_minutes) + ' known minutes</strong> · ' +
    esc(review.observed_playing_days) + ' playing days over 28 days</p>' +
    '<p class="evidence">Effort logged on ' + esc(review.effort_logged_days) +
    ' playing days · ' + esc(review.missing_dates.length) +
    ' scheduled past dates unobserved. Today’s evening is not marked missing.</p>' +
    (review.days.length ? '<details><summary>Review match dates and logs</summary>' +
      '<div class="table-wrap"><table><thead><tr><th>Date</th><th>Minutes</th><th>Effort</th><th>Source</th></tr></thead><tbody>' +
      [...review.days].reverse().map(day => '<tr><td><button type="button" class="activity-link" data-checkin="' +
        esc(day.date) + '">' + esc(day.date) + '</button></td><td>' +
        (day.minutes == null ? 'Unknown' : esc(day.minutes)) +
        (day.duration_complete ? '' : day.status === 'recorded' ? ' · partial' : '') +
        '</td><td>' + (day.rpe == null ? '—' : 'RPE ' + esc(day.rpe)) +
        '</td><td>' + esc(day.status === 'logged skipped' ? 'Logged skipped' : day.source) +
        (day.conflict ? '<br><span class="evidence">Garmin recording also exists; check this log.</span>' : '') +
        '</td></tr>').join('') + '</tbody></table></div></details>' : '') +
    '<p class="evidence">' + esc(review.note) + '</p>';
}
$("football-review").onclick = (event) => {
  const button = event.target.closest("[data-checkin]");
  if (!button) return;
  $("date").value = button.dataset.checkin;
  fillCheckin();
  $("checkin-dialog").showModal();
  $("date").focus({ preventScroll: true });
};
function fillCheckin() {
  const entry = reportData?.metrics.feedback.find(
    (item) => item.date === $("date").value,
  );
  for (const key of [
    "fatigue",
    "soreness",
    "pain",
    "football_minutes",
    "football_rpe",
    "notes",
  ])
    $(key).value = entry?.[key] ?? "";
  $("feedback-status").textContent = entry
    ? "Saved check-in loaded. Saving replaces this date's check-in."
    : "No check-in saved for this date yet.";
}
$("date").onchange = fillCheckin;
function renderCheckinHistory() {
  const entries = [...reportData.metrics.feedback]
    .sort((a, b) => b.date.localeCompare(a.date))
    .slice(0, 14);
  $("checkin-history").innerHTML = entries.length
    ? '<details class="checkin-history"><summary>Recent check-ins · ' +
      entries.length +
      "</summary><ul>" +
      entries
        .map(
          (entry) =>
            '<li><button type="button" class="activity-link" data-checkin="' +
            esc(entry.date) +
            '">Edit ' +
            esc(entry.date) +
            '</button><p class="evidence">Fatigue ' +
            esc(entry.fatigue) +
            " · soreness " +
            esc(entry.soreness) +
            " · pain " +
            esc(entry.pain) +
            (entry.football_minutes == null
              ? ""
              : " · football " +
                esc(entry.football_minutes) +
                " min" +
                (entry.football_rpe == null
                  ? ""
                  : " at RPE " + esc(entry.football_rpe))) +
            "</p></li>",
        )
        .join("") +
      "</ul></details>"
    : '<p class="evidence">Your saved check-ins will appear here. Choose each rating; 0 means none and 10 means your highest severity.</p>';
}
$("checkin-history").onclick = (event) => {
  const button = event.target.closest("[data-checkin]");
  if (!button) return;
  $("date").value = button.dataset.checkin;
  fillCheckin();
  $("date").focus();
  $("checkin-dialog").showModal();
};
async function load() {
  try {
    const response = await fetch(appUrl("/api/report"), {
      headers: reportEtag ? { "If-None-Match": reportEtag } : {},
    });
    if (response.status === 304) return;
    if (!response.ok) {
      const error = await response.json();
      throw Error(error.error || "Could not load local data");
    }
    reportEtag = response.headers.get("ETag");
    const data = await response.json();
    render(data);
    lastGenerated = data.generated_at;
  } catch (e) {
    status(e.message, true);
  }
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const s = await get("/api/status");
    await refreshCalendarStatus();
    $("refresh").disabled = s.busy;
    $("full").disabled = s.busy;
    $("cancel").classList.toggle("hidden", !s.busy && !s.mfa);
    $("mfa").classList.toggle("hidden", !s.mfa);
    if (s.mfa && !$('verification-dialog').open) $('verification-dialog').showModal();
    if (!s.mfa && $('verification-dialog').open) $('verification-dialog').close();
    const run = s.sync.run,
      telemetry = run?.telemetry || {};
    const incomplete = ["partial", "interrupted"].includes(run?.status);
    let message = s.message;
    if (!s.busy && !s.mfa && message === "Ready")
      message = reportData?.metrics.activities.length
        ? "Your plan is up to date with saved Garmin data."
        : "Refresh Garmin to build your plan from recorded training.";
    if (incomplete && !s.busy)
      message = "Refresh incomplete: " + (run.message || "retry to resume");
    status(s.error || message, !!s.error || incomplete);
    const total = telemetry.total || 0,
      completed = telemetry.completed || 0;
    $("sync-progress").classList.toggle("hidden", !s.busy || !total);
    $("progress").max = total || 1;
    $("progress").value = Math.min(total, completed);
    $("progress-detail").textContent =
      (telemetry.phase || "Import").replaceAll("_", " ") +
      " · " +
      completed +
      "/" +
      total +
      " · " +
      (telemetry.requests || 0) +
      " requests · " +
      (telemetry.cache_hits || 0) +
      " cached";
    if (
      (lastBusy && !s.busy) ||
      (s.as_of && reportData?.as_of !== s.as_of && !s.busy) ||
      (s.report_generated_at && s.report_generated_at !== lastGenerated)
    )
      await load();
    lastBusy = s.busy;
  } catch (e) {
    status(
      "Dashboard disconnected. Restart the local server and reload.",
      true,
    );
  } finally {
    polling = false;
  }
}
$("cancel").onclick = async () => {
  try {
    await post("/api/cancel", {});
    await poll();
  } catch (e) {
    status(e.message, true);
  }
};
$("refresh").onclick = async () => {
  try {
    await post("/api/refresh", { full: $("full").checked });
    lastBusy = true;
    await poll();
  } catch (e) {
    status(e.message, true);
  }
};
$("mfa").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await post("/api/refresh", {
      code: $("code").value,
      full: $("full").checked,
    });
    $("code").value = "";
    lastBusy = true;
    await poll();
  } catch (e) {
    status(e.message, true);
  }
};
$("feedback").onsubmit = async (e) => {
  e.preventDefault();
  const body = { date: $("date").value, notes: $("notes").value };
  for (const key of [
    "fatigue",
    "soreness",
    "pain",
    "football_minutes",
    "football_rpe",
  ])
    body[key] = $(key).value === "" ? null : Number($(key).value);
  try {
    $("save").disabled = true;
    const response = await post("/api/feedback", body);
    $("feedback-status").textContent = response.message;
    await load();
  } catch (e) {
    $("feedback-status").textContent = e.message;
  } finally {
    $("save").disabled = false;
  }
};
$("filter").onchange = () => {
  shown = 30;
  renderActivities();
};
$("more").onclick = () => {
  shown += 30;
  renderActivities();
};
load().then(poll);
refreshCalendarStatus();
$('garmin-connect').onsubmit = async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  button.disabled = true;
  const body = {email:$('garmin-email').value,password:$('garmin-password').value};
  $('garmin-password').value = '';
  try {
    const value = await post('/api/garmin/connect', body);
    $('garmin-message').textContent = value.message;
  } catch (error) { $('garmin-message').textContent = error.message; }
  finally { button.disabled = false; }
};
$('calendar-connect').onsubmit = async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  button.disabled = true;
  $('calendar-message').textContent = 'Connecting to iCloud…';
  try {
    const body = {username:$('apple-email').value,password:$('apple-password').value};
    $('apple-password').value = '';
    const value = await post('/api/calendar/connect', body);
    $('calendar-message').textContent = value.message + '. Upcoming runs synced.';
    await refreshCalendarStatus();
  } catch (error) { $('calendar-message').textContent = error.message; }
  finally { button.disabled = false; }
};
$('calendar-sync').onclick = async () => {
  $('calendar-sync').disabled = true;
  try {
    const value = await post('/api/calendar/sync', {});
    $('calendar-message').textContent = value.connected ? 'Calendar updated: ' + value.created + ' added, ' + value.updated + ' changed, ' + value.deleted + ' removed.' : value.message;
    await refreshCalendarStatus();
  } catch (error) { $('calendar-message').textContent = error.message; }
  finally { $('calendar-sync').disabled = false; }
};
setInterval(poll, 2000);

function setupChoices(r) {
  const records = r.metrics.garmin_records || [],
    record5k = r.metrics.garmin_5k_record;
  $("garmin-records").innerHTML = records.length
    ? '<div class="result-history">' +
      (record5k
        ? "<p><strong>Garmin 5K record · " +
          esc(durationTime(record5k.seconds)) +
          "</strong> · " +
          esc(record5k.date || "date unknown") +
          "</p>"
        : "") +
      '<p class="evidence">Saved accepted Garmin records provide historical context. Use a fresh measured result to choose current goals; course and effort are unverified.</p><details><summary>Saved running records · ' +
      records.length +
      '</summary><div class="table-wrap"><table><thead><tr><th>Distance</th><th>Record</th><th>Date</th></tr></thead><tbody>' +
      records
        .map(
          (record) =>
            "<tr><td>" +
            esc(record.label) +
            "</td><td>" +
            esc(
              record.seconds == null
                ? record.distance_km.toFixed(2) + " km"
                : durationTime(record.seconds),
            ) +
            "</td><td>" +
            esc(record.date || "unknown") +
            "</td></tr>",
        )
        .join("") +
      "</tbody></table></div></details></div>"
    : "";
  const weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  if (!$("running-days").children.length) {
    const preferences = r.metrics.preferences || {};
    for (const [id, selected] of [
      ["running-days", preferences.running_days || [2, 3, 5]],
      ["football-days", preferences.football_days || [0, 6]],
    ])
      $(id).innerHTML = weekdays
        .map(
          (day, index) =>
            '<label><input type="checkbox" value="' +
            index +
            '" ' +
            (selected.includes(index) ? "checked" : "") +
            ">" +
            day +
            "</label>",
        )
        .join("");
    $("target-date").value = r.plan.target_date;
    $("gym-access").checked = preferences.gym !== false;
  }
  $("result-date").max = r.as_of;
  if (!$("result-date").value) $("result-date").value = r.as_of;
  $("clear-benchmark").disabled = !r.metrics.confirmed_benchmark;
}
$("preferences-form").onsubmit = async (event) => {
  event.preventDefault();
  const values = (id) =>
    Array.from($(id).querySelectorAll("input:checked")).map((input) =>
      Number(input.value),
    );
  try {
    const response = await post("/api/preferences", {
      target_date: $("target-date").value,
      running_days: values("running-days"),
      football_days: values("football-days"),
      gym: $("gym-access").checked,
    });
    $("preferences-status").textContent = response.message;
    await load();
  } catch (error) {
    $("preferences-status").textContent = error.message;
  }
};
$("benchmark-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const response = await post("/api/benchmark", {
      date: $("result-date").value,
      seconds:
        Number($("result-minutes").value) * 60 +
        Number($("result-seconds").value),
      kind: $("result-kind").value,
      measured_course: $("measured-course").checked,
      note: $("result-note").value,
    });
    $("benchmark-status").textContent = response.message;
    await load();
  } catch (error) {
    $("benchmark-status").textContent = error.message;
  }
};
$("clear-benchmark").onclick = async () => {
  try {
    const response = await post("/api/benchmark", { clear: true });
    $("benchmark-status").textContent = response.message;
    await load();
  } catch (error) {
    $("benchmark-status").textContent = error.message;
  }
};

function lineChart(points, key, label, unit) {
  const valid = points.filter((point) => point[key] != null);
  if (!valid.length)
    return '<p class="empty">No recorded ' + esc(label) + " channel.</p>";
  const low = Math.min(...valid.map((point) => point[key]));
  const high = Math.max(...valid.map((point) => point[key]));
  const end = Math.max(1, ...points.map((point) => point.minutes));
  const range = Math.max(1, high - low);
  let path = "",
    dots = "",
    connected = false;
  for (const point of points) {
    if (point.gap_before?.includes(key)) connected = false;
    if (point[key] == null) {
      connected = false;
      continue;
    }
    const x = 45 + (point.minutes / end) * 610;
    const y = 155 - ((point[key] - low) / range) * 120;
    if (!connected)
      dots +=
        '<circle cx="' +
        x.toFixed(1) +
        '" cy="' +
        y.toFixed(1) +
        '" r="2.5" fill="#245c45"><title>' +
        esc(point.minutes.toFixed(2) + " min · " + point[key] + " " + unit) +
        "</title></circle>";
    path += (connected ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1) + " ";
    connected = true;
  }
  return (
    "<h3>" +
    esc(label) +
    '</h3><svg viewBox="0 0 700 190" role="img" aria-label="' +
    esc(label + ", " + low.toFixed(0) + " to " + high.toFixed(0) + " " + unit) +
    '"><line x1="45" y1="155" x2="655" y2="155" stroke="#d7ded3"/><text x="0" y="35" font-size="12" fill="#61736b">' +
    esc(high.toFixed(0)) +
    '</text><text x="0" y="155" font-size="12" fill="#61736b">' +
    esc(low.toFixed(0)) +
    '</text><path d="' +
    path +
    '" fill="none" stroke="#245c45" stroke-width="2"/>' +
    dots +
    '<text x="45" y="180" font-size="12" fill="#61736b">0 min</text><text x="655" y="180" text-anchor="end" font-size="12" fill="#61736b">' +
    end.toFixed(1) +
    " min</text></svg>"
  );
}
let activityRequest = 0;
async function openActivity(id) {
  const request = ++activityRequest;
  const dialog = $("activity-detail");
  $("activity-heading").textContent = "Activity details";
  $("activity-content").textContent = "Loading recorded samples…";
  if (!dialog.open) dialog.showModal();
  try {
    const detail = await get("/api/activities/" + encodeURIComponent(id));
    if (request !== activityRequest) return;
    const activity = detail.activity;
    $("activity-heading").textContent = activity.name;
    $("activity-content").innerHTML =
      '<p class="muted">' +
      esc(activity.date + " · " + activity.sport) +
      '</p><div class="detail-stats"><p><strong>' +
      esc(activity.km == null ? "—" : activity.km.toFixed(2) + " km") +
      "</strong><br>Recorded distance</p><p><strong>" +
      durationTime(activity.elapsed_seconds || activity.duration_seconds) +
      "</strong><br>Elapsed time</p><p><strong>" +
      esc(
        activity.average_hr == null
          ? "—"
          : activity.average_hr.toFixed(0) + " bpm",
      ) +
      "</strong><br>Average heart rate</p></div>" +
      lineChart(
        detail.samples,
        "heart_rate",
        "Heart rate over recorded activity time",
        "bpm",
      ) +
      (activity.kind === "run"
        ? lineChart(
            detail.samples,
            "pace_seconds_km",
            "Recorded sample pace",
            "seconds/km",
          )
        : "") +
      (activity.kind === "run"
        ? '<p class="evidence">Running sample pace converts speed to seconds/km. Lower values mean faster running; GPS noise and stops affect it.</p>'
        : "") +
      '<h3>Recorded laps</h3><div class="table-wrap"><table><thead><tr><th>Lap</th><th>Distance</th><th>Elapsed</th><th>Pace / km</th><th>Average HR</th></tr></thead><tbody>' +
      detail.laps
        .map(
          (lap) =>
            "<tr><td>" +
            lap.index +
            "</td><td>" +
            esc(
              lap.distance_km == null
                ? "—"
                : lap.distance_km.toFixed(2) + " km",
            ) +
            "</td><td>" +
            durationTime(lap.seconds) +
            "</td><td>" +
            time(lap.pace_seconds_km) +
            "</td><td>" +
            esc(lap.average_hr == null ? "—" : lap.average_hr.toFixed(0)) +
            "</td></tr>",
        )
        .join("") +
      (detail.laps.length
        ? ""
        : '<tr><td colspan="5">No recorded laps available.</td></tr>') +
      '</tbody></table></div><p class="evidence">' +
      (detail.sample_quality
        ? esc(
            "Timeline sources: " +
              detail.sample_quality.time_bases.join(", ") +
              ". " +
              detail.sample_quality.large_time_gaps +
              " unusually long time gaps; " +
              detail.sample_quality.excluded_timeline_rows +
              " rows without usable time excluded. ",
          )
        : "") +
      esc(detail.note) +
      " Displaying " +
      detail.display_sample_count +
      " of " +
      detail.source_sample_count +
      " usable source samples.</p>";
  } catch (error) {
    if (request === activityRequest)
      $("activity-content").textContent = error.message;
  }
}
$("activities").onclick = (event) => {
  const button = event.target.closest("[data-activity]");
  if (button) openActivity(button.dataset.activity);
};
$("activity-search").oninput = () => {
  shown = 30;
  renderActivities();
};
$("close-activity").onclick = () => {
  activityRequest++;
  $("activity-detail").close();
};

function renderWeek(p) {
  weekOffset = Math.min(weekOffset, Math.max(0, p.sessions.length - 1));
  $("week").innerHTML = p.sessions
    .slice(weekOffset, weekOffset + 7)
    .map(
      (s, i) =>
        '<button class="day" data-kind="' +
        esc(s.kind) +
        '" data-index="' +
        (i + weekOffset) +
        '"><span class="eyebrow">' +
        esc(s.weekday.slice(0, 3)) +
        "</span><br>" +
        esc(s.date.slice(5)) +
        "<strong>" +
        esc(s.title) +
        "</strong>" +
        esc(
          s.km
            ? (s.kind === "quality" ? "Up to " : "") + s.km + " km"
            : s.minutes
              ? s.minutes + " min"
              : s.kind === "football"
                ? "Evening"
                : "Recovery",
        ) +
        "</button>",
    )
    .join("");
  document
    .querySelectorAll(".day")
    .forEach((el) => (el.onclick = () => { choose(Number(el.dataset.index)); $("session-dialog").showModal(); }));
  const index = p.sessions.findIndex((s) => s.date === chosenDay);
  choose(index >= weekOffset && index < weekOffset + 7 ? index : weekOffset);
  $("week-prev").disabled = weekOffset === 0;
  $("week-next").disabled = weekOffset + 7 >= p.sessions.length;
  const rows = p.sessions.slice(weekOffset, weekOffset + 7);
  $("week-range").textContent = rows.length
    ? rows[0].date + " → " + rows[rows.length - 1].date
    : "No future sessions";
  $("week-heading").textContent =
    weekOffset === 0 ? "The next seven days" : "Your upcoming sessions";
  $("forecast-note").textContent =
    weekOffset > 0
      ? "Forecast: refresh near these dates to adapt to your actual training and recovery."
      : "";
}

$("week-prev").onclick = () => {
  weekOffset = Math.max(0, weekOffset - 7);
  renderWeek(reportData.plan);
};
$("week-next").onclick = () => {
  weekOffset += 7;
  renderWeek(reportData.plan);
};

$("backup").onclick = async () => {
  $("backup").disabled = true;
  try {
    const result = await post("/api/backup", {});
    $("backup-status").textContent = result.message + ": " + result.path;
  } catch (error) {
    $("backup-status").textContent = error.message;
  } finally {
    $("backup").disabled = false;
  }
};

function renderRecovery(r) {
  const key = $("recovery-metric").value,
    label = $("recovery-metric").selectedOptions[0].textContent;
  const days = Number($("recovery-range").value);
  $("recovery-heading").textContent =
    "Recovery over the last " + days + " days";
  const start = new Date(r.as_of + "T00:00:00Z");
  start.setUTCDate(start.getUTCDate() - days + 1);
  const rows = r.metrics.daily.filter(
    (day) =>
      day.date >= start.toISOString().slice(0, 10) && day.date <= r.as_of,
  );
  $("sleep-chart").innerHTML = chart(rows, key, label);
  $("recovery-readings").innerHTML =
    rows
      .slice()
      .reverse()
      .map(
        (day) =>
          "<tr><td>" +
          esc(day.date) +
          "</td><td>" +
          esc(day[key] ?? "No reading") +
          "</td></tr>",
      )
      .join("") || '<tr><td colspan="2">No recovery data imported.</td></tr>';
}
$("recovery-metric").onchange = () => {
  if (reportData) renderRecovery(reportData);
};

function renderHistory(r) {
  const count = Number($("history-range").value),
    rows = (r.metrics.history_weeks || r.metrics.weeks).slice(-count);
  $("chart").innerHTML = chart(rows, "run_km", "Recorded running kilometres") +
    '<details class="chart-readings"><summary>View running weeks</summary><div class="table-wrap"><table><thead><tr><th>Week</th><th>Run km</th><th>Runs</th></tr></thead><tbody>' +
    [...rows].reverse().map(row => '<tr><td>' + esc(row.start) + ' to ' + esc(row.end) +
      '</td><td>' + esc(row.run_km) + '</td><td>' + esc(row.run_count) + '</td></tr>').join('') +
    '</tbody></table></div></details><p class="evidence">Zero means no running distance recorded in that window. It can also reflect missing recordings.</p>';
}
$("history-range").onchange = () => {
  if (reportData) {
    renderHistory(reportData);
    const detail = document.querySelector('#chart details');
    if (detail) $('supporting-readings').replaceChildren(detail, ...document.querySelectorAll('#supporting-readings details:not(.chart-readings)'));
  }
};

$("recovery-range").onchange = () => {
  if (reportData) renderRecovery(reportData);
};


// Three viewport-sized views; supporting forms remain available in dialogs.
const verification = document.createElement('dialog');
verification.id = 'verification-dialog'; verification.setAttribute('aria-label', 'Garmin verification');
verification.append($('mfa')); document.body.append(verification);
let insightPage = 0;
const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(index) {
  tabs.forEach((tab, i) => {
    tab.setAttribute('aria-selected', String(i === index));
    tab.tabIndex = i === index ? 0 : -1;
    $('view-' + i).hidden = i !== index;
  });
  sessionStorage.setItem('fivek-tab', String(index));
}
tabs.forEach((tab, index) => {
  tab.onclick = () => selectTab(index);
  tab.onkeydown = event => {
    let next = index;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else return;
    event.preventDefault(); selectTab(next); tabs[next].focus();
  };
});
selectTab(Math.max(0, Math.min(2, Number(sessionStorage.getItem('fivek-tab')) || 0)));
document.querySelectorAll('[data-open-dialog]').forEach(button => {
  button.onclick = () => $(button.dataset.openDialog).showModal();
});
document.querySelectorAll('[data-close-dialog]').forEach(button => {
  button.onclick = () => button.closest('dialog').close();
});
// Keep Escape and native focus restoration for all supporting dialogs.
function renderOverview(r) {
  $('focus-days').innerHTML = r.plan.sessions.slice(0, 2).map((s, i) =>
    '<article class="focus-card"><span class="eyebrow">' + (i ? 'Tomorrow' : 'Today') +
    ' · ' + esc(s.date) + '</span><h2>' + esc(s.title) + '</h2><p>' +
    esc(s.next_action || s.details.split('. ')[0] + '.') + '</p><div class="focus-bottom"><span class="badge">' +
    esc(s.status === 'recorded' ? 'Recorded' : s.effort) + '</span><button type="button" class="secondary" data-focus="' + i +
    '">Session & strength details</button></div></article>').join('');
  document.querySelectorAll('[data-focus]').forEach(button => button.onclick = () => {
    choose(Number(button.dataset.focus)); $('session-dialog').showModal();
  });
  const recent = r.metrics.activities.filter(a => a.date >= (() => {
    const d = new Date(r.as_of + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate()-27); return d.toISOString().slice(0,10);
  })() && a.date <= r.as_of);
  const sports = [
    ['Running', a => a.kind === 'run'],
    ['Strength', a => a.sport === 'strength_training'],
    ['Football', a => a.kind === 'football'],
  ];
  $('workload-summary').innerHTML = '<div class="sport-totals">' + sports.map(([label, match]) => {
    const rows = recent.filter(match), minutes = rows.reduce((sum,a) => sum + (a.minutes || 0), 0);
    return '<div><strong>' + Math.round(minutes) + ' min</strong><span>' + label + ' · ' + rows.length + ' recorded sessions</span></div>';
  }).join('') + '</div>';
  $('workload-chart').innerHTML = chart(r.metrics.weeks.slice(-4), 'run_km', 'Running kilometres · last four weeks');
  // Paginate insights instead of hiding or clipping longer findings.
  const box = $('insights');
  const articles = [...box.querySelectorAll('.insight')];
  const controls = document.createElement('div'); controls.className = 'insight-controls';
  controls.innerHTML = '<button class="secondary" type="button" aria-label="Previous insight">←</button><span aria-live="polite"></span><button class="secondary" type="button" aria-label="Next insight">→</button>';
  box.append(controls);
  const show = () => {
    insightPage = Math.max(0, Math.min(articles.length-1, insightPage));
    articles.forEach((article,i) => article.hidden = i !== insightPage);
    controls.querySelector('span').textContent = articles.length ? (insightPage+1) + ' / ' + articles.length : 'No insights yet';
    controls.firstElementChild.disabled = insightPage <= 0;
    controls.lastElementChild.disabled = insightPage >= articles.length-1;
  };
  controls.firstElementChild.onclick = () => { insightPage--; show(); };
  controls.lastElementChild.onclick = () => { insightPage++; show(); };
  show();
  let readings = $('supporting-readings');
  if (!readings) {
    readings = document.createElement('section'); readings.id = 'supporting-readings'; readings.className = 'panel';
    $('evidence-dialog').append(readings);
  }
  readings.replaceChildren();
  for (const selector of ['#chart details', '#football-review details']) {
    const detail = document.querySelector(selector);
    if (detail) readings.append(detail);
  }
}
