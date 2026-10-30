const $ = s => document.querySelector(s);
const sleep = ms => new Promise(r => setTimeout(r, ms));
let stream = null, autoTimer = null, busy = false;

async function api(url, opts = {}) {
  const r = await fetch(url, { credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, ...opts });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || d.message || "Something went wrong");
  return d;
}
const post = (url, body) => api(url, { method: "POST", body: JSON.stringify(body) });

// ---------- Auth ----------
async function boot() {
  try { showApp(await api("/api/me")); } catch { $("#loginView").hidden = false; }
}
function showApp(u) {
  $("#loginView").hidden = true; $("#appView").hidden = false;
  $("#who").textContent = `${u.user} (${u.role})`;
  startCamera();
}
$("#loginBtn").onclick = async () => {
  try { showApp(await post("/api/login", { username: $("#lu").value, password: $("#lp").value })); }
  catch (e) { $("#loginErr").textContent = e.message; }
};
$("#lp").onkeydown = e => { if (e.key === "Enter") $("#loginBtn").click(); };
$("#logoutBtn").onclick = async () => { await post("/api/logout", {}); location.reload(); };

// ---------- Tabs ----------
document.querySelectorAll(".tab").forEach(t => t.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === t));
  document.querySelectorAll(".pane").forEach(p => p.hidden = p.id !== t.dataset.tab);
  if (t.dataset.tab === "reg") loadStudents();
  if (t.dataset.tab === "rep") loadReports();
});

// ---------- Camera ----------
async function startCamera() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
    $("#vidMark").srcObject = stream; $("#vidReg").srcObject = stream;
  } catch { $("#result").textContent = "Allow camera access in your browser."; }
}
const cv = document.createElement("canvas");
function grab() {
  const v = $("#vidMark").srcObject ? ($("#vidMark").offsetParent ? $("#vidMark") : $("#vidReg")) : $("#vidReg");
  cv.width = v.videoWidth; cv.height = v.videoHeight;
  cv.getContext("2d").drawImage(v, 0, 0);
  return cv.toDataURL("image/jpeg", 0.8);
}
async function burst(n, gap) {
  const frames = [];
  for (let i = 0; i < n; i++) { frames.push(grab()); await sleep(gap); }
  return frames;
}

// ---------- Mark attendance ----------
async function scan() {
  if (busy) return;
  const subject = $("#subject").value.trim();
  const box = $("#result");
  if (!subject) { box.className = "result bad"; box.textContent = "Enter a subject first."; return; }
  busy = true; $("#scanBtn").disabled = true; $("#scanLine").classList.add("on");
  box.className = "result"; box.textContent = "Scanning... blink once";
  try {
    const frames = await burst(8, 200);
    const r = await post("/api/recognize", { subject, frames });
    box.className = "result " + (r.status === "marked" ? "marked" : r.status === "already" ? "already" : "bad");
    box.textContent = r.message + (r.confidence ? `  [match ${r.confidence}%]` : "");
  } catch (e) { box.className = "result bad"; box.textContent = e.message; }
  busy = false; $("#scanBtn").disabled = false; $("#scanLine").classList.remove("on");
}
$("#scanBtn").onclick = scan;
$("#auto").onchange = e => {
  clearInterval(autoTimer);
  if (e.target.checked) autoTimer = setInterval(scan, 5000);
};

// ---------- Register ----------
$("#regBtn").onclick = async () => {
  const roll = $("#rRoll").value.trim(), name = $("#rName").value.trim();
  const msg = $("#regMsg");
  if (!roll || !name) { msg.textContent = "Roll number and name are required."; return; }
  $("#regBtn").disabled = true; msg.textContent = "Capturing... look at the camera";
  try {
    const frames = await burst(6, 400);
    await post("/api/students", { roll, name, email: $("#rEmail").value,
      parent_email: $("#rParent").value, password: $("#rPass").value, frames });
    msg.textContent = `${name} registered.`;
    ["#rRoll", "#rName", "#rEmail", "#rParent", "#rPass"].forEach(s => $(s).value = "");
    loadStudents();
  } catch (e) { msg.textContent = e.message; }
  $("#regBtn").disabled = false;
};
async function loadStudents() {
  const rows = await api("/api/students");
  $("#stuTable").innerHTML = "<tr><th>Roll</th><th>Name</th><th>Email</th><th>Parent email</th><th></th></tr>" +
    (rows.map(s => `<tr><td>${esc(s.roll)}</td><td>${esc(s.name)}</td><td>${esc(s.email)}</td>
      <td>${esc(s.parent_email)}</td><td><button class="ghost" onclick="resetPw(${s.id})">Reset password</button>
      <button class="ghost" onclick="delStu(${s.id})">Delete</button></td></tr>`).join("")
      || "<tr><td colspan=5>No students yet. Register the first one above.</td></tr>");
}
window.delStu = async id => {
  if (!confirm("Delete this student and their attendance?")) return;
  await api("/api/students/" + id, { method: "DELETE" }); loadStudents();
};

window.resetPw = async id => {
  const p = prompt("New password for this student:");
  if (!p) return;
  try { await post(`/api/students/${id}/password`, { password: p }); alert("Password updated."); }
  catch (e) { alert(e.message); }
};

// ---------- Reports ----------
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const qs = () => `date=${$("#fDate").value}&subject=${encodeURIComponent($("#fSub").value)}`;

async function loadReports() {
  const [log, sum] = await Promise.all([api("/api/attendance?" + qs()), api("/api/summary")]);
  $("#logTable").innerHTML = "<tr><th>Date</th><th>Time</th><th>Subject</th><th>Roll</th><th>Name</th></tr>" +
    (log.map(r => `<tr><td>${r.date}</td><td>${r.time}</td><td>${esc(r.subject)}</td><td>${esc(r.roll)}</td><td>${esc(r.name)}</td></tr>`).join("")
      || "<tr><td colspan=5>No records for this filter.</td></tr>");
  const thr = +$("#thr").value;
  $("#sumTable").innerHTML = "<tr><th>Roll</th><th>Name</th><th>Present</th><th>Total classes</th><th>Percent</th></tr>" +
    sum.students.map(s => `<tr><td>${esc(s.roll)}</td><td>${esc(s.name)}</td><td>${s.present}</td><td>${s.total}</td>
      <td class="${s.total && s.percent < thr ? "low" : ""}">${s.percent}%</td></tr>`).join("");
  drawChart(sum.trend);
}
function drawChart(data) {
  const c = $("#chart"), g = c.getContext("2d"), css = getComputedStyle(document.body);
  g.clearRect(0, 0, c.width, c.height);
  const max = Math.max(1, ...data.map(d => d.count)), bw = c.width / data.length;
  g.font = "13px sans-serif"; g.textAlign = "center";
  data.forEach((d, i) => {
    const h = (d.count / max) * (c.height - 50), x = i * bw + bw * 0.2;
    g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--brand");
    g.fillRect(x, c.height - 30 - h, bw * 0.6, h);
    g.fillStyle = css.color;
    g.fillText(d.count, x + bw * 0.3, c.height - 34 - h);
    g.fillText(d.date, x + bw * 0.3, c.height - 10);
  });
}
$("#loadBtn").onclick = loadReports;
$("#csvBtn").onclick = () => location.href = "/api/export?" + qs();
$("#alertBtn").onclick = async () => {
  try {
    const r = await post("/api/alerts", { threshold: $("#thr").value });
    $("#alertMsg").textContent = r.low.length
      ? `Low attendance: ${r.low.join(", ")}. ` + (r.smtp_configured ? `${r.emails_sent} email(s) sent.` : "SMTP is not configured, so no emails were sent (see README).")
      : "All students are above the threshold.";
  } catch (e) { $("#alertMsg").textContent = e.message; }
};

boot();
