/* MixLens front-end: no frameworks, no external chart libraries. */
(() => {
  "use strict";

  const CAT_VARS = ["--blue", "--orange", "--aqua", "--yellow", "--magenta", "--violet", "--red"];
  const state = {
    result: null,
    loading: false,
    requestSeq: 0,
    theme: localStorage.getItem("mixlens-theme") || "light",
  };

  const $ = (id) => document.getElementById(id);
  const debounce = (fn, ms = 250) => {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  };
  const scheduleRun = debounce(runAnalysis, 320);
  const fmt = (x, digits = 3) => {
    if (x === null || x === undefined || Number.isNaN(x)) return "—";
    if (typeof x !== "number") return String(x);
    if (Math.abs(x) >= 1000) return x.toFixed(0);
    if (Math.abs(x) >= 10) return x.toFixed(2);
    return x.toFixed(digits);
  };
  const pct = (x, digits = 1) => (x === null || x === undefined || Number.isNaN(x) ? "—" : `${(100 * x).toFixed(digits)}%`);
  const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const catColor = (i) => token(CAT_VARS[i % CAT_VARS.length]);
  const alphaColor = (hex, alpha) => {
    if (!hex || hex[0] !== "#") return hex;
    const n = parseInt(hex.slice(1), 16);
    const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return `rgba(${r},${g},${b},${alpha})`;
  };

  function setStatus(message, kind = "") {
    const el = $("statusBanner");
    el.className = `status-banner ${kind}`.trim();
    el.innerHTML = kind === "loading"
      ? `<span class="spinner" aria-hidden="true"></span><span>${message}</span>`
      : `<span>${message}</span>`;
  }

  function setLoading(loading) {
    state.loading = loading;
    [$("runBtn"), $("runBtnTop")].forEach((b) => { b.disabled = loading; });
    $("runBtn").textContent = loading ? "分析中…" : "运行分析";
  }

  function defaultConfig() {
    return {
      scenario: "independent",
      seed: 818,
      n_samples: 1600,
      sample_rate: 100,
      n_channels: 4,
      n_sources: 3,
      noise: 0.018,
      missing_enabled: false,
      visible_channels: [0, 1, 2, 3],
      train_start: 96,
      train_end: 880,
      eval_start: 960,
      eval_end: 1504,
      component_count: "auto",
      n_starts: 6,
      max_iter: 120,
      swap_enabled: false,
      recalibrate_enabled: true,
      swap_channels: [0, 1],
      injection: { enabled: false, kind: "pulse", amplitude: 0.9, spatial: "uniform" },
    };
  }

  function collectConfig() {
    const nChannels = parseInt($("nChannels").value, 10);
    const visible = Array.from(document.querySelectorAll('input[name="channelToggle"]:checked'))
      .map((x) => parseInt(x.value, 10));
    return {
      scenario: $("scenario").value,
      seed: parseInt($("seed").value || "818", 10),
      n_samples: parseInt($("nSamples").value, 10),
      sample_rate: 100,
      n_channels: nChannels,
      n_sources: parseInt($("nSources").value, 10),
      noise: parseFloat($("noise").value),
      missing_enabled: $("missingEnabled").checked,
      visible_channels: visible,
      train_start: parseInt($("trainStart").value, 10),
      train_end: parseInt($("trainEnd").value, 10),
      eval_start: parseInt($("evalStart").value, 10),
      eval_end: parseInt($("evalEnd").value, 10),
      component_count: $("componentCount").value,
      n_starts: parseInt($("nStarts").value, 10),
      max_iter: 120,
      swap_enabled: $("swapEnabled").checked,
      recalibrate_enabled: $("recalibrateEnabled").checked,
      swap_channels: [parseInt($("swapA").value || "0", 10), parseInt($("swapB").value || "1", 10)],
      injection: {
        enabled: $("injectionEnabled").checked,
        kind: $("injectionKind").value,
        amplitude: parseFloat($("injectionAmplitude").value),
        spatial: $("injectionSpatial").value,
      },
    };
  }

  function applyConfigToControls(cfg) {
    $("scenario").value = cfg.scenario;
    $("seed").value = cfg.seed;
    $("nSamples").value = cfg.n_samples;
    $("nChannels").value = String(cfg.n_channels);
    $("nSources").value = String(cfg.n_sources);
    $("noise").value = String(cfg.noise);
    $("missingEnabled").checked = cfg.missing_enabled;
    syncRangeBounds();
    $("trainStart").value = cfg.train_start;
    $("trainEnd").value = cfg.train_end;
    $("evalStart").value = cfg.eval_start;
    $("evalEnd").value = cfg.eval_end;
    $("componentCount").value = String(cfg.component_count);
    $("nStarts").value = String(cfg.n_starts);
    $("swapEnabled").checked = cfg.swap_enabled;
    $("recalibrateEnabled").checked = cfg.recalibrate_enabled !== false;
    $("injectionEnabled").checked = cfg.injection.enabled;
    $("injectionKind").value = cfg.injection.kind;
    $("injectionAmplitude").value = String(cfg.injection.amplitude);
    $("injectionSpatial").value = cfg.injection.spatial;
    renderChannelControls();
    if ($("swapA").options.length > cfg.swap_channels[0]) $("swapA").value = String(cfg.swap_channels[0]);
    if ($("swapB").options.length > cfg.swap_channels[1]) $("swapB").value = String(cfg.swap_channels[1]);
    updateControlLabels();
  }

  function updateControlLabels() {
    $("noiseValue").textContent = parseFloat($("noise").value).toFixed(3);
    $("nStartsValue").textContent = $("nStarts").value;
    $("injectionValue").textContent = parseFloat($("injectionAmplitude").value).toFixed(2);
    $("trainStartValue").textContent = $("trainStart").value;
    $("trainEndValue").textContent = $("trainEnd").value;
    $("evalStartValue").textContent = $("evalStart").value;
    $("evalEndValue").textContent = $("evalEnd").value;
  }

  function syncRangeBounds() {
    const n = Math.max(100, parseInt($("nSamples").value || "1600", 10));
    for (const id of ["trainStart", "evalStart"]) $(id).max = String(n - 24);
    for (const id of ["trainEnd", "evalEnd"]) $(id).max = String(n);
  }

  function renderChannelControls(result = state.result, resetChecked = false) {
    const n = parseInt($("nChannels").value, 10);
    const oldChecked = new Set(
      Array.from(document.querySelectorAll('input[name="channelToggle"]:checked')).map((x) => x.value)
    );
    const diagByIndex = new Map((result?.diagnostics?.channels || []).map((c) => [c.channel_index, c]));
    const list = $("channelList");
    list.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const label = document.createElement("label");
      label.className = "check-pill";
      const d = diagByIndex.get(i);
      if (d?.constant) label.classList.add("constant");
      const checked = resetChecked || !oldChecked.size ? true : oldChecked.has(String(i));
      const meta = d
        ? (d.constant ? "常量" : `SD ${fmt(d.std, 2)}${d.missing_percent ? ` · 缺 ${d.missing_percent.toFixed(0)}%` : ""}`)
        : `CH ${i + 1}`;
      label.innerHTML = `
        <input type="checkbox" name="channelToggle" value="${i}" ${checked ? "checked" : ""} />
        <span class="swatch" style="background:${catColor(i)}"></span>
        <span>${d?.name || `传感器 ${i + 1}`}</span>
        <span class="meta">${meta}${d?.unit ? ` ${d.unit}` : ""}</span>`;
      list.appendChild(label);
    }
    for (const id of ["swapA", "swapB"]) {
      const sel = $(id);
      const old = sel.value;
      sel.innerHTML = "";
      for (let i = 0; i < n; i++) {
        const opt = document.createElement("option");
        opt.value = String(i);
        opt.textContent = `传感器 ${i + 1}`;
        sel.appendChild(opt);
      }
      if (old && parseInt(old, 10) < n) sel.value = old;
    }
    if (!$("swapA").value) $("swapA").value = "0";
    if (!$("swapB").value) $("swapB").value = n > 1 ? "1" : "0";
  }

  const PRESETS = {
    independent: { nSources: 3, nChannels: 4, missing: false, swap: false },
    correlated: { nSources: 3, nChannels: 4, missing: false, swap: false },
    gaussian: { nSources: 2, nChannels: 3, missing: false, swap: false },
    rankdef: { nSources: 3, nChannels: 4, missing: false, swap: false },
    constant: { nSources: 3, nChannels: 4, missing: false, swap: false },
    missing: { nSources: 3, nChannels: 4, missing: true, swap: false },
    permutation: { nSources: 3, nChannels: 4, missing: false, swap: true, a: 0, b: 1 },
  };

  function applyScenarioPreset() {
    const p = PRESETS[$("scenario").value] || {};
    if (p.nSources) $("nSources").value = String(p.nSources);
    if (p.nChannels) $("nChannels").value = String(p.nChannels);
    if (p.missing !== undefined) $("missingEnabled").checked = p.missing;
    if (p.swap !== undefined) $("swapEnabled").checked = p.swap;
    syncRangeBounds();
    const n = parseInt($("nSamples").value, 10);
    $("trainStart").value = Math.round(0.06 * n);
    $("trainEnd").value = Math.round(0.55 * n);
    $("evalStart").value = Math.round(0.60 * n);
    $("evalEnd").value = Math.round(0.94 * n);
    renderChannelControls(null, true);
    if (p.a !== undefined) $("swapA").value = String(p.a);
    if (p.b !== undefined) $("swapB").value = String(p.b);
    updateControlLabels();
    scheduleRun();
  }

  async function runAnalysis() {
    if (state.loading) return;
    const seq = ++state.requestSeq;
    setLoading(true);
    setStatus("正在诊断量纲/缺测/秩，并运行多起点 ICA…", "loading");
    const config = collectConfig();
    state.config = config;
    try {
      const resp = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      const data = await resp.json();
      if (seq !== state.requestSeq) return;
      if (!resp.ok || !data.ok) throw new Error(data.error || `请求失败 (${resp.status})`);
      state.result = data;
      renderAll();
      setStatus(`分析完成：${data.dataset.n_samples} 个样本，有效秩 ${data.diagnostics.effective_rank}，恢复判定“${recoveryText(data.analysis.recovery.label)}”。`, "success");
    } catch (err) {
      if (seq !== state.requestSeq) return;
      state.result = null;
      setStatus(err.message || String(err), "error");
    } finally {
      if (seq === state.requestSeq) setLoading(false);
    }
  }

  function recoveryText(label) {
    return {
      matched: "匹配良好",
      partial: "部分/不确定",
      not_identifiable: "不可辨识",
      uncertain_gaussian: "高斯旋转不确定",
    }[label] || label;
  }

  function renderAll() {
    const r = state.result;
    if (!r) return;
    renderChannelControls(r);
    renderKpis(r);
    renderDiagnostics(r);
    renderInterpretation(r);
    renderObsChannelSelect(r);
    renderComponentShells(r);
    renderMixing(r);
    renderStability(r);
    renderTables(r);
    renderCharts();
  }

  function renderKpis(r) {
    const a = r.analysis;
    const rec = a.recovery;
    const evalR2 = a.evaluation?.recalibrated?.metrics?.mean_r2;
    const stab = a.stability.min_match_to_selected;
    const recClass = rec.label === "uncertain_gaussian"
      ? "warn"
      : rec.min_abs_correlation >= 0.9 ? "good" : rec.min_abs_correlation >= 0.72 ? "warn" : "bad";
    const r2Class = (x) => (x === null || x === undefined ? "" : x >= 0.9 ? "good" : x >= 0.7 ? "warn" : "bad");
    const stabClass = stab >= 0.88 ? "good" : stab >= 0.75 ? "warn" : "bad";
    const cards = [
      ["最低来源 |r|", fmt(rec.min_abs_correlation, 3), recoveryText(rec.label), recClass],
      ["训练段重建 R²", pct(a.training.metrics.mean_r2, 1), "原始量纲最小二乘混合", r2Class(a.training.metrics.mean_r2)],
      ["后续段 R²", pct(evalR2, 1), a.swap ? "显式重标定后" : "固定系数", r2Class(evalR2)],
      ["最低起点匹配", fmt(stab, 3), "顺序/符号匹配后", stabClass],
      ["有效秩 / 分量", `${r.diagnostics.effective_rank} / ${a.rank.component_count}`, `条件数 ${fmt(r.diagnostics.condition_number, 1)}`, ""],
      ["缺测单元", r.diagnostics.total_missing_cells, `常量通道 ${r.diagnostics.constant_channels.length ? r.diagnostics.constant_channels.map((i) => i + 1).join(",") : "无"}`, r.diagnostics.total_missing_cells ? "warn" : "good"],
    ];
    $("kpiGrid").innerHTML = cards.map(([label, value, sub, cls]) => `
      <div class="kpi ${cls}">
        <div class="label">${label}</div>
        <div class="value">${value}</div>
        <div class="sub" title="${sub}">${sub}</div>
      </div>`).join("");
  }

  function renderDiagnostics(r) {
    const d = r.diagnostics;
    const cards = [
      ["数据维度", `${d.n_channels_visible}/${d.n_channels_total} 通道`, `${d.n_samples} 样本；拟合使用 ${d.n_channels_used} 路`],
      ["白化有效秩", `${d.effective_rank}`, `候选来源 ${d.candidate_sources}；λ1=${fmt(d.eigenvalues[0], 2)}`],
      ["训练完整样本", `${d.train_complete_samples}`, `训练段 ${d.train_length}；缺测按完整行排除`],
      ["条件数", fmt(d.condition_number, 1), d.condition_number > 100 ? "弱方向/噪声敏感" : "保留方向总体稳定"],
    ];
    $("diagCards").innerHTML = cards.map(([l, v, h]) => `
      <div class="mini-stat"><div class="label">${l}</div><div class="value">${v}</div><div class="hint">${h}</div></div>`).join("");

    const fs = r.dataset.sample_rate;
    $("channelDiagTable").querySelector("tbody").innerHTML = d.channels.map((c) => {
      const ranges = c.missing_ranges.length
        ? c.missing_ranges.map((x) => `${x.start}–${x.end} (${(x.start / fs).toFixed(1)}–${(x.end / fs).toFixed(1)}s)`).join("<br>")
        : "—";
      const status = c.constant
        ? '<span class="badge bad">常量/排除</span>'
        : c.missing_count > 0 ? '<span class="badge warn">缺测</span>' : '<span class="badge good">可用</span>';
      return `<tr>
        <td><span class="legend-item"><span class="swatch" style="width:9px;height:9px;border-radius:50%;display:inline-block;background:${catColor(c.channel_index)}"></span>${c.name}</span></td>
        <td>${c.unit}</td>
        <td class="num">${c.available_samples}/${c.total_samples}</td>
        <td class="num">${fmt(c.missing_percent, 2)}%</td>
        <td class="num">${fmt(c.mean, 3)}</td>
        <td class="num">${fmt(c.std, 3)}</td>
        <td>${ranges}</td>
        <td>${status}</td>
      </tr>`;
    }).join("");
  }

  function renderInterpretation(r) {
    const icons = { success: "✓", warning: "!", critical: "×", info: "i" };
    $("interpretationList").innerHTML = r.analysis.interpretation.statuses.map((s) => `
      <div class="status-item ${s.level}">
        <div class="status-icon" aria-hidden="true">${icons[s.level] || "i"}</div>
        <div><h3>${s.title}</h3><p>${s.detail}</p></div>
      </div>`).join("");
  }

  function renderObsChannelSelect(r) {
    const sel = $("obsChannelSelect");
    const current = sel.value;
    const ids = r.analysis.selected_channels?.length ? r.analysis.selected_channels : r.analysis.used_channels;
    sel.innerHTML = ids.map((i) => `<option value="${i}">${r.dataset.channels[i].name} (${r.dataset.channels[i].unit})</option>`).join("");
    if (current && ids.includes(parseInt(current, 10))) sel.value = current;
    else if (r.analysis.used_channels.length) sel.value = String(r.analysis.used_channels[0]);
  }

  function fullSegmentSeries(r, segment, rowIndex, kind = "components") {
    const n = r.dataset.n_samples;
    const out = new Array(n).fill(null);
    if (!segment || rowIndex === undefined || rowIndex < 0) return out;
    const [lo] = segment.range;
    const rows = segment[kind];
    const row = rows?.[rowIndex];
    if (!row) return out;
    for (let i = 0; i < row.length; i++) out[lo + i] = row[i];
    return out;
  }

  function assembleTruthAligned(r, sourceIndex) {
    const n = r.dataset.n_samples;
    const out = new Array(n).fill(null);
    const train = r.analysis.training?.components_truth_aligned?.[sourceIndex];
    if (train) train.forEach((v, i) => { out[r.analysis.train_range[0] + i] = v; });
    const evalSeg = r.analysis.evaluation?.recalibrated;
    const ev = evalSeg?.components_truth_aligned?.[sourceIndex];
    if (ev) ev.forEach((v, i) => { out[r.analysis.eval_range[0] + i] = v; });
    return out;
  }

  function drawObsChart() {
    const r = state.result;
    if (!r) return;
    const globalCh = parseInt($("obsChannelSelect").value || "0", 10);
    const usedPos = r.analysis.used_channels.indexOf(globalCh);
    const ch = r.dataset.channels[globalCh];
    const series = [];
    if ($("showObserved").checked) {
      series.push({ name: `观测 ${ch.name}`, values: ch.values, color: token("--blue"), width: 1.8 });
    }
    if (usedPos >= 0 && $("showRecon").checked) {
      const recalEnabled = $("recalibrateEnabled").checked;
      const evalPrimary = (!r.analysis.swap?.active || recalEnabled)
        ? r.analysis.evaluation?.recalibrated
        : r.analysis.evaluation?.naive;
      const recon = new Array(r.dataset.n_samples).fill(null);
      const res = new Array(r.dataset.n_samples).fill(null);
      for (const [seg, range] of [
        [r.analysis.training, r.analysis.train_range],
        [evalPrimary, r.analysis.eval_range],
      ]) {
        if (!seg) continue;
        const rr = seg.reconstruction?.[usedPos] || [];
        const ee = seg.residual?.[usedPos] || [];
        rr.forEach((v, i) => { recon[range[0] + i] = v; });
        ee.forEach((v, i) => { res[range[0] + i] = v; });
      }
      const reconName = r.analysis.swap?.active && !recalEnabled
        ? "固定关系重建（未重标定）"
        : r.analysis.swap?.active ? "固定关系重建（已显式重标定）" : "固定关系重建";
      series.push({ name: reconName, values: recon, color: token("--orange"), width: 1.7 });
      $("residualWrap").hidden = !$("showResidual").checked;
      if ($("showResidual").checked) {
        drawLineChart($("residualChart"), {
          fs: r.dataset.sample_rate,
          series: [{ name: "残差（原始单位）", values: res, color: token("--critical"), width: 1.3 }],
          bands: defaultBands(r),
          zero: true,
        });
        bindTooltip($("residualChart"), $("residualTooltip"));
      }
    } else {
      $("residualWrap").hidden = true;
    }
    if (usedPos >= 0 && $("showNaive").checked && $("recalibrateEnabled").checked && r.analysis.evaluation?.naive) {
      const naive = new Array(r.dataset.n_samples).fill(null);
      const seg = r.analysis.evaluation.naive;
      const rr = seg.reconstruction?.[usedPos] || [];
      rr.forEach((v, i) => { naive[r.analysis.eval_range[0] + i] = v; });
      series.push({ name: "未重标定直接套用", values: naive, color: token("--violet"), width: 1.6, dash: [6, 4] });
    }
    drawLineChart($("obsChart"), { fs: r.dataset.sample_rate, series, bands: defaultBands(r), zero: false });
    bindTooltip($("obsChart"), $("obsTooltip"));
    $("obsLegend").innerHTML = series.map((s) => `
      <span class="legend-item"><span class="legend-line ${s.dash ? "dashed" : ""}" style="background:${s.dash ? "transparent" : s.color};color:${s.color}"></span>${s.name}</span>`).join("")
      + `<span class="legend-item"><span style="width:12px;height:12px;background:var(--train-band);border:1px solid ${alphaColor(token("--blue"), .25)};border-radius:3px;"></span>训练段</span>
         <span class="legend-item"><span style="width:12px;height:12px;background:var(--eval-band);border:1px solid ${alphaColor(token("--aqua"), .25)};border-radius:3px;"></span>后续检验段</span>`;
  }

  function defaultBands(r) {
    return [
      { start: r.analysis.train_range[0], end: r.analysis.train_range[1], color: token("--train-band") },
      { start: r.analysis.eval_range[0], end: r.analysis.eval_range[1], color: token("--eval-band") },
    ];
  }

  function renderComponentShells(r) {
    const k = r.dataset.true_sources.length;
    const grid = $("componentGrid");
    grid.innerHTML = "";
    for (let j = 0; j < k; j++) {
      const match = r.analysis.matching.train.find((m) => m.reference_index === j);
      const corr = match ? match.abs_correlation : 0;
      const corrCls = corr >= 0.9 ? "good" : corr >= 0.72 ? "warn" : "bad";
      const card = document.createElement("article");
      card.className = "component-card";
      card.innerHTML = `
        <div class="component-title">
          <span class="swatch" style="width:11px;height:11px;border-radius:3px;background:${catColor(j)}"></span>
          <span>${r.dataset.true_sources[j].name}</span>
          ${r.dataset.true_sources[j].id === "injected" ? '<span class="badge info">已知注入</span>' : ""}
          <span class="corr"><span class="badge ${corrCls}">${match ? `|r|=${fmt(corr, 3)}` : "未匹配"}</span></span>
        </div>
        <div class="chart-wrap">
          <canvas id="compChart${j}" class="chart mini" role="img" aria-label="分量与真值 ${j + 1}"></canvas>
          <div id="compTooltip${j}" class="tooltip"></div>
        </div>`;
      grid.appendChild(card);
    }
  }

  function drawComponentCharts() {
    const r = state.result;
    if (!r) return;
    const k = r.dataset.true_sources.length;
    for (let j = 0; j < k; j++) {
      const canvas = $(`compChart${j}`);
      if (!canvas) continue;
      const truth = r.dataset.true_sources[j].values;
      const estimated = assembleTruthAligned(r, j);
      drawLineChart(canvas, {
        fs: r.dataset.sample_rate,
        series: [
          { name: "合成真值（虚线）", values: truth, color: token("--text-2"), width: 1.3, dash: [5, 4] },
          { name: "匹配/尺度校正后估计", values: estimated, color: catColor(j), width: 1.8 },
        ],
        bands: defaultBands(r),
        zero: true,
      });
      bindTooltip(canvas, $(`compTooltip${j}`));
    }
  }

  function niceTicks(min, max, count = 5) {
    if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
      min = (min || 0) - 1; max = (max || 0) + 1;
    }
    const span = max - min;
    const pad = span * 0.08 || 1;
    min -= pad; max += pad;
    const step0 = (max - min) / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const norm = step0 / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const start = Math.ceil(min / step) * step;
    const ticks = [];
    for (let v = start; v <= max + step * 0.5; v += step) ticks.push(v);
    return { min, max, ticks };
  }

  function setupCanvas(canvas) {
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(280, rect.width || canvas.parentElement.clientWidth || 600);
    const h = Math.max(120, rect.height || parseInt(getComputedStyle(canvas).height) || 260);
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }

  function drawLineChart(canvas, options) {
    if (!canvas) return;
    const { ctx, w, h } = setupCanvas(canvas);
    const pad = { l: 50, r: 14, t: 12, b: 26 };
    const pw = w - pad.l - pad.r;
    const ph = h - pad.t - pad.b;
    const allValues = [];
    let maxIndex = 0;
    for (const s of options.series || []) {
      const start = s.start || 0;
      maxIndex = Math.max(maxIndex, start + (s.values?.length || 1) - 1);
      for (const v of s.values || []) if (typeof v === "number" && Number.isFinite(v)) allValues.push(v);
    }
    const xMin = options.xMin ?? 0;
    const xMax = options.xMax ?? Math.max(1, maxIndex);
    let yMin = options.yMin ?? Math.min(...allValues, 0);
    let yMax = options.yMax ?? Math.max(...allValues, 0);
    if (!allValues.length) { yMin = -1; yMax = 1; }
    const yNice = niceTicks(yMin, yMax, 5);
    yMin = yNice.min; yMax = yNice.max;
    const mapX = (x) => pad.l + ((x - xMin) / Math.max(1e-9, xMax - xMin)) * pw;
    const mapY = (y) => pad.t + (1 - (y - yMin) / (yMax - yMin)) * ph;

    ctx.font = "11px system-ui, sans-serif";
    ctx.lineWidth = 1;
    ctx.strokeStyle = token("--grid");
    ctx.fillStyle = token("--muted");
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (const tick of yNice.ticks) {
      const y = mapY(tick);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + pw, y); ctx.stroke();
      ctx.fillText(Math.abs(tick) < 1e-10 ? "0" : fmt(tick, Math.abs(tick) >= 10 ? 1 : 2), pad.l - 7, y);
    }

    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const xTickCount = Math.min(7, Math.max(3, Math.round(pw / 110)));
    for (let i = 0; i <= xTickCount; i++) {
      const xVal = xMin + (i / xTickCount) * (xMax - xMin);
      const x = mapX(xVal);
      ctx.strokeStyle = alphaColor(token("--axis"), 0.45);
      ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ph); ctx.stroke();
      let label = String(Math.round(xVal));
      if (options.fs) label = `${(xVal / options.fs).toFixed(xMax / options.fs > 20 ? 0 : 1)}s`;
      ctx.fillStyle = token("--muted");
      ctx.fillText(label, x, pad.t + ph + 7);
    }

    for (const band of options.bands || []) {
      const x1 = mapX(Math.max(xMin, band.start));
      const x2 = mapX(Math.min(xMax, band.end));
      ctx.fillStyle = band.color;
      ctx.fillRect(x1, pad.t, Math.max(0, x2 - x1), ph);
    }

    if (options.zero !== false && yMin < 0 && yMax > 0) {
      const y0 = mapY(0);
      ctx.strokeStyle = token("--axis");
      ctx.setLineDash([3, 4]);
      ctx.beginPath(); ctx.moveTo(pad.l, y0); ctx.lineTo(pad.l + pw, y0); ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.save();
    ctx.beginPath();
    ctx.rect(pad.l, pad.t, pw, ph);
    ctx.clip();
    for (const s of options.series || []) {
      const start = s.start || 0;
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width || 1.7;
      ctx.lineJoin = "round";
      ctx.setLineDash(s.dash || []);
      ctx.beginPath();
      let pen = false;
      for (let i = 0; i < (s.values?.length || 0); i++) {
        const v = s.values[i];
        if (v === null || v === undefined || !Number.isFinite(v)) { pen = false; continue; }
        const x = mapX(start + i);
        const y = mapY(v);
        if (!pen) { ctx.moveTo(x, y); pen = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
    ctx.restore();
    ctx.setLineDash([]);

    ctx.strokeStyle = token("--axis");
    ctx.beginPath(); ctx.moveTo(pad.l, pad.t); ctx.lineTo(pad.l, pad.t + ph); ctx.lineTo(pad.l + pw, pad.t + ph); ctx.stroke();

    canvas._hit = { pad, w, h, pw, ph, xMin, xMax, yMin, yMax, series: options.series || [], fs: options.fs };
  }

  function bindTooltip(canvas, tooltip) {
    if (!canvas || !tooltip) return;
    canvas.onmousemove = (ev) => {
      const hit = canvas._hit;
      if (!hit) return;
      const rect = canvas.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const y = ev.clientY - rect.top;
      if (x < hit.pad.l || x > hit.pad.l + hit.pw || y < hit.pad.t || y > hit.pad.t + hit.ph) {
        tooltip.style.opacity = "0"; return;
      }
      const xVal = hit.xMin + ((x - hit.pad.l) / hit.pw) * (hit.xMax - hit.xMin);
      const idx = Math.max(0, Math.min(Math.round(hit.xMax), Math.round(xVal)));
      const title = hit.fs ? `t=${(idx / hit.fs).toFixed(2)}s · sample ${idx}` : `完整样本序号 ${idx}`;
      let rows = "";
      for (const s of hit.series) {
        const start = s.start || 0;
        const local = idx - start;
        const v = local >= 0 ? s.values?.[local] : null;
        if (typeof v === "number" && Number.isFinite(v)) {
          rows += `<div class="tt-row"><span class="tt-dot" style="background:${s.color}"></span><span>${s.name}</span><strong style="margin-left:auto;padding-left:10px;font-variant-numeric:tabular-nums">${fmt(v, 4)}</strong></div>`;
        }
      }
      tooltip.innerHTML = `<div class="tt-title">${title}</div>${rows}`;
      tooltip.style.opacity = "1";
      const left = Math.min(Math.max(70, x), hit.w - 90);
      const top = Math.max(42, y - 8);
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    };
    canvas.onmouseleave = () => { tooltip.style.opacity = "0"; };
  }

  function divergingBackground(value, maxAbs) {
    if (value === null || value === undefined || !Number.isFinite(value)) return token("--surface-2");
    const a = 0.08 + 0.58 * Math.min(1, Math.abs(value) / maxAbs);
    return value >= 0 ? alphaColor(token("--critical"), a) : alphaColor(token("--blue"), a);
  }

  function renderMatrixTable(container, matrix, rowLabels, colLabels, maxAbs) {
    if (!matrix || !matrix.length) { container.innerHTML = "<p class='help'>无矩阵</p>"; return; }
    if (maxAbs === undefined) {
      maxAbs = 1e-9;
      matrix.forEach((row) => row.forEach((v) => { if (Number.isFinite(v)) maxAbs = Math.max(maxAbs, Math.abs(v)); }));
    }
    const head = `<tr><th></th>${colLabels.map((l) => `<th>${l}</th>`).join("")}</tr>`;
    const body = matrix.map((row, i) => `<tr>
      <th class="rowhead">${rowLabels[i] || `R${i + 1}`}</th>
      ${row.map((v) => `<td class="heat-cell" style="background:${divergingBackground(v, maxAbs)}" title="${fmt(v, 6)}">${v === null ? "—" : fmt(v, 2)}</td>`).join("")}
    </tr>`).join("");
    container.innerHTML = `<table class="heatmap-table"><thead>${head}</thead><tbody>${body}</tbody></table>`;
  }

  function renderMixing(r) {
    const used = r.analysis.used_channels;
    const rowLabels = used.map((i) => `${r.dataset.channels[i].name}<br><span style="font-weight:400">${r.dataset.channels[i].unit}</span>`);
    const colLabels = r.dataset.true_sources.map((s, j) => `S${j + 1}${s.id === "injected" ? "★" : ""}`);
    const trueM = r.analysis.matching.mixing_true_used;
    const estM = r.analysis.matching.mixing_estimated_truth_aligned;
    let maxAbs = 1e-9;
    [...trueM, ...estM].forEach((row) => row.forEach((v) => { if (Number.isFinite(v)) maxAbs = Math.max(maxAbs, Math.abs(v)); }));
    renderMatrixTable($("trueMixTable"), trueM, rowLabels, colLabels, maxAbs);
    renderMatrixTable($("estMixTable"), estM, rowLabels, colLabels, maxAbs);
    $("mixMetricTable").querySelector("tbody").innerHTML = (r.analysis.matching.mixing_metrics || []).map((m) => `
      <tr><td>${r.dataset.true_sources[m.source_index].name}</td><td>C${m.component_index + 1}</td>
      <td class="num">${fmt(m.cosine_similarity, 4)}</td><td class="num">${fmt(m.relative_column_norm, 3)}</td></tr>`).join("")
      || '<tr><td colspan="4">无匹配列</td></tr>';
  }

  function renderStability(r) {
    const s = r.analysis.stability;
    const matrix = s.pairwise_match;
    $("stabilityHeatmap").innerHTML = `<table class="heatmap-table">
      <tbody>${matrix.map((row, i) => `<tr>
        <th class="rowhead">#${i + 1}${s.runs[i]?.is_selected ? " ★" : ""}</th>
        ${row.map((v) => `<td class="heat-cell" style="background:${alphaColor(token("--blue"), 0.10 + 0.62 * v)};color:${v > 0.72 ? "white" : token("--text")}">${fmt(v, 2)}</td>`).join("")}
      </tr>`).join("")}</tbody></table>`;
    const sel = $("overlayComponent");
    const q = r.analysis.rank.component_count;
    sel.innerHTML = Array.from({ length: q }, (_, i) => `<option value="${i}">参考分量 C${i + 1}</option>`).join("");
    sel.onchange = drawStabilityOverlay;
    $("runTable").querySelector("tbody").innerHTML = s.runs.map((run) => `
      <tr${run.is_selected ? ' style="background:var(--train-band)"' : ""}>
        <td>${run.start_index + 1}${run.is_selected ? " ★" : ""}</td><td class="num">${run.seed}</td>
        <td class="num">${run.iterations}</td>
        <td>${run.converged ? '<span class="badge good">收敛</span>' : '<span class="badge warn">未达阈值</span>'}</td>
        <td class="num">${fmt(run.match_to_selected, 4)}</td>
        <td>${run.is_selected ? "按目标值选中" : "已匹配参考"}</td>
      </tr>`).join("");
    drawStabilityOverlay();
  }

  function drawStabilityOverlay() {
    const r = state.result;
    if (!r) return;
    const c = parseInt($("overlayComponent").value || "0", 10);
    const alignedRuns = r.analysis.stability_aligned_components || [];
    const series = alignedRuns.map((runRows, i) => {
      const selected = r.analysis.stability.runs[i]?.is_selected;
      return {
        name: `起点 ${i + 1}${selected ? "（选中）" : ""}`,
        values: runRows[c] || [],
        color: selected ? token("--orange") : alphaColor(token("--blue"), 0.30),
        width: selected ? 2.2 : 1.1,
      };
    });
    drawLineChart($("stabilityOverlay"), { series, zero: true });
    bindTooltip($("stabilityOverlay"), $("stabilityTooltip"));
  }

  function renderIndependence(r) {
    const a = r.analysis;
    const kurt = a.component_diagnostics.excess_kurtosis || [];
    const gaussianLike = kurt.filter((k) => Math.abs(k) < 0.35).length;
    const maxMi = a.component_diagnostics.max_pairwise_mi || 0;
    const trueCorr = a.source_dependence.max_abs_pearson || 0;
    const kurtBadges = kurt.map((k, i) => {
      const cls = Math.abs(k) < 0.35 ? "warn" : "good";
      return `<span class="badge ${cls}" style="margin-right:5px;">C${i + 1} κ=${fmt(k, 2)}</span>`;
    }).join("");
    $("independenceBox").innerHTML = `<table class="data">
      <thead><tr><th>独立性/高斯性审计</th><th>数值</th><th>解释</th></tr></thead>
      <tbody>
        <tr><td>估计分量超额峰度</td><td>${kurtBadges}</td><td>接近 0 表示近高斯；当前 ${gaussianLike} 个方向低于经验阈值 0.35。</td></tr>
        <tr><td>估计分量最大归一化互信息</td><td class="num">${fmt(maxMi, 3)}</td><td>白化/ICA 后线性相关应接近零；该指标用于检查残留非线性依赖。</td></tr>
        <tr><td>合成真值最大 |Pearson r|</td><td class="num"><span class="badge ${trueCorr >= .55 ? "bad" : "good"}">${fmt(trueCorr, 3)}</span></td><td>${trueCorr >= .55 ? "真实来源显著相关，标准 ICA 独立性前提不成立。" : "真实来源近似独立；此数值只有合成审计中可知。"}</td></tr>
      </tbody>
    </table>`;
  }

  function renderTables(r) {
    const a = r.analysis;
    renderIndependence(r);
    $("matchTable").querySelector("tbody").innerHTML = a.matching.train.map((m) => {
      const source = m.reference_index === null || m.reference_index === undefined ? null : r.dataset.true_sources[m.reference_index];
      const cls = m.abs_correlation >= 0.9 ? "good" : m.abs_correlation >= 0.72 ? "warn" : "bad";
      return `<tr>
        <td>C${m.estimated_index + 1} <span class="muted">sign ${m.sign > 0 ? "+" : "−"}</span></td>
        <td>${source ? source.name : "未匹配"}</td>
        <td class="num">${fmt(m.correlation, 4)}</td>
        <td class="num"><span class="badge ${cls}">${fmt(m.abs_correlation, 3)}</span></td>
        <td class="num">${fmt(m.gain_to_truth, 3)}</td>
        <td>${m.abs_correlation >= 0.9 ? "可对应" : m.abs_correlation >= .72 ? "部分/谨慎" : "不可命名"}</td>
      </tr>`;
    }).join("");

    const rows = [];
    const pushMetricRows = (title, seg, aggregate) => {
      if (!seg) return;
      rows.push(`<tr style="background:var(--surface-2)"><td><strong>${title} · 汇总</strong></td><td class="num">${seg.complete_samples ?? "—"}</td>
        <td class="num"><strong>${pct(seg.metrics?.mean_r2, 2)}</strong></td><td class="num">${fmt(seg.metrics?.mean_nrmse_std, 3)}</td><td>—</td></tr>`);
      (seg.channel_metrics || []).forEach((m) => rows.push(`<tr>
        <td>${title} / ${m.name}</td><td class="num">${m.complete_samples}</td><td class="num">${fmt(m.r2, 4)}</td>
        <td class="num">${fmt(m.nrmse_std, 3)}</td><td class="num">${fmt(m.residual_lag1, 3)}</td></tr>`));
    };
    pushMetricRows("训练", a.training);
    if (a.evaluation) {
      pushMetricRows("后续·重标定", a.evaluation.recalibrated);
      if (a.evaluation.naive) pushMetricRows("后续·未重标定", a.evaluation.naive);
    }
    $("segmentMetricTable").querySelector("tbody").innerHTML = rows.join("");

    const pm = a.pipeline_matrices;
    $("rawMatrices").textContent = [
      `mean = ${JSON.stringify(pm.mean, null, 0)}`,
      `scale = ${JSON.stringify(pm.scale, null, 0)}`,
      `whitening = ${formatMatrix(pm.whitening)}`,
      `W_ica = ${formatMatrix(pm.w_ica)}`,
      `A_standardized = ${formatMatrix(pm.a_standardized)}`,
      `A_raw_original_units = ${formatMatrix(pm.a_raw_original_units)}`,
    ].join("\n\n");
  }

  function formatMatrix(m) {
    return "\n" + (m || []).map((row) => "  [" + row.map((v) => (Number.isFinite(v) ? v.toFixed(6) : "null")).join(", ") + "]").join("\n");
  }

  function renderCharts() {
    if (!state.result) return;
    drawObsChart();
    drawComponentCharts();
    drawStabilityOverlay();
  }

  async function runAudit() {
    const panel = $("auditPanel");
    panel.classList.add("open");
    $("auditContent").innerHTML = '<p><span class="spinner"></span> 正在运行 7 个本地反例…</p>';
    try {
      const resp = await fetch("/api/audit");
      const data = await resp.json();
      if (!resp.ok || !data.ok) throw new Error(data.error || "审计失败");
      const passedCases = data.results.filter((x) => x.ok).length;
      $("auditContent").innerHTML = `
        <div class="audit-summary">
          <span class="badge ${data.ok ? "good" : "bad"}">${data.ok ? "关键核对全部通过" : "存在未通过关键项"}</span>
          <strong>${passedCases}/${data.results.length} 个场景关键检查通过</strong>
          <span class="help" style="margin:0">相关/高斯反例中的“恢复失败或不确定”本身是预期核对项。</span>
        </div>
        ${data.results.map(renderAuditCase).join("")}`;
    } catch (err) {
      $("auditContent").innerHTML = `<p class="status-banner error">${err.message}</p>`;
    }
  }

  function renderAuditCase(c) {
    if (c.error) {
      return `<div class="audit-case"><div class="audit-case-head"><h3>${c.label}</h3><span class="badge bad">运行错误</span></div><p>${c.error}</p></div>`;
    }
    const m = c.metrics || {};
    return `<div class="audit-case">
      <div class="audit-case-head">
        <h3>${c.label}</h3>
        <span class="badge ${c.ok ? "good" : "bad"}">${c.ok ? "关键通过" : "需检查"}</span>
        <span class="badge info">恢复 ${m.recovery || "—"}</span>
        <span class="badge info">min|r| ${fmt(m.min_source_correlation, 3)}</span>
        <span class="badge info">R² ${fmt(m.mean_train_r2, 3)}</span>
        ${m.naive_eval_r2 !== null && m.naive_eval_r2 !== undefined ? `<span class="badge warn">未重标定 ${fmt(m.naive_eval_r2, 3)} → ${fmt(m.mean_eval_r2, 3)}</span>` : ""}
      </div>
      <ul class="audit-checks">${(c.checks || []).map((x) => `
        <li><span class="${x.passed ? "pass" : "fail"}">${x.passed ? "✓" : "×"}</span>
        <span><strong>${x.label}</strong> — ${typeof x.observed === "string" ? x.observed : JSON.stringify(x.observed)}${x.explanation ? `<br><span class="help">${x.explanation}</span>` : ""}</span></li>`).join("")}
      </ul>
    </div>`;
  }

  function download(filename, content, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function csvCell(v) {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function exportCsv() {
    const r = state.result;
    if (!r) return;
    const n = r.dataset.n_samples;
    const a = r.analysis;
    const used = a.used_channels;
    const header = ["sample_index", "time_s", "segment"];
    r.dataset.channels.forEach((c, i) => header.push(`observed_${i + 1}_${c.unit}`));
    const q = a.rank.component_count;
    for (let i = 0; i < q; i++) header.push(`raw_component_C${i + 1}`);
    r.dataset.true_sources.forEach((s, j) => {
      header.push(`true_source_${j + 1}_${s.kind}`);
      header.push(`estimate_matched_to_source_${j + 1}`);
    });

    const rawComp = Array.from({ length: q }, () => new Array(n).fill(null));
    a.training.components.forEach((row, i) => row.forEach((v, t) => { rawComp[i][a.train_range[0] + t] = v; }));
    a.evaluation?.recalibrated?.components.forEach((row, i) => row.forEach((v, t) => { rawComp[i][a.eval_range[0] + t] = v; }));
    const aligned = r.dataset.true_sources.map((_, j) => assembleTruthAligned(r, j));

    const lines = [header.map(csvCell).join(",")];
    for (let t = 0; t < n; t++) {
      const segment = t >= a.train_range[0] && t < a.train_range[1] ? "train"
        : t >= a.eval_range[0] && t < a.eval_range[1] ? "evaluation_fixed" : "context";
      const row = [t, (t / r.dataset.sample_rate).toFixed(4), segment];
      r.dataset.channels.forEach((c) => row.push(c.values[t]));
      for (let i = 0; i < q; i++) row.push(rawComp[i][t]);
      r.dataset.true_sources.forEach((s, j) => {
        row.push(s.values[t]);
        row.push(aligned[j][t]);
      });
      lines.push(row.map(csvCell).join(","));
    }
    download(`mixlens_components_seed${r.dataset.seed}.csv`, "\uFEFF" + lines.join("\n"), "text/csv;charset=utf-8");
  }

  function exportJson() {
    const r = state.result;
    if (!r) return;
    const payload = {
      app: "MixLens blind source separation workbench",
      exported_with: "Python stdlib + vanilla JS",
      config_request: state.config || collectConfig(),
      actual_synthetic_parameters: {
        scenario: r.dataset.scenario,
        seed: r.dataset.seed,
        n_samples: r.dataset.n_samples,
        sample_rate: r.dataset.sample_rate,
        channel_name: r.dataset.channels.map((c) => c.name),
        channel_unit: r.dataset.channel_unit,
        channel_gain: r.dataset.channel_gain,
        channel_offset: r.dataset.channel_offset,
        noise_sd: r.dataset.noise_sd,
        mixing_base: r.dataset.mixing_base,
        mixing_true_raw_original_units: r.dataset.mixing_true,
        true_source_definitions: r.dataset.true_sources.map(({ values, ...meta }) => meta),
        injection: r.dataset.injection,
        known_structure: r.dataset.known_structure,
      },
      estimated_pipeline: r.analysis.pipeline_matrices,
      matching: r.analysis.matching,
      recovery: r.analysis.recovery,
      source_dependence_audit: r.analysis.source_dependence,
      component_diagnostics: r.analysis.component_diagnostics,
      stability: r.analysis.stability,
      diagnostics_summary: {
        effective_rank: r.diagnostics.effective_rank,
        eigenvalues: r.diagnostics.eigenvalues,
        condition_number: r.diagnostics.condition_number,
        constant_channels: r.diagnostics.constant_channels,
        total_missing_cells: r.diagnostics.total_missing_cells,
        channel_diagnostics: r.diagnostics.channels,
      },
      metrics: {
        training: r.analysis.training.metrics,
        training_channels: r.analysis.training.channel_metrics,
        evaluation: r.analysis.evaluation ? {
          range: r.analysis.evaluation.range,
          channel_map: r.analysis.evaluation.channel_map,
          recalibrated: r.analysis.evaluation.recalibrated.metrics,
          recalibrated_channels: r.analysis.evaluation.recalibrated.channel_metrics,
          naive: r.analysis.evaluation.naive ? r.analysis.evaluation.naive.metrics : null,
          naive_channels: r.analysis.evaluation.naive ? r.analysis.evaluation.naive.channel_metrics : null,
        } : null,
      },
    };
    download(`mixlens_parameters_seed${r.dataset.seed}.json`, JSON.stringify(payload, null, 2), "application/json;charset=utf-8");
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("mixlens-theme", theme);
    $("themeBtn").textContent = theme === "dark" ? "切换浅色" : "切换深色";
    $("themeBtn").setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    if (state.result) renderAll();
  }

  function initEvents() {
    $("runBtn").addEventListener("click", runAnalysis);
    $("runBtnTop").addEventListener("click", runAnalysis);
    $("auditBtn").addEventListener("click", runAudit);
    $("closeAuditBtn").addEventListener("click", () => $("auditPanel").classList.remove("open"));
    $("exportCsvBtn").addEventListener("click", exportCsv);
    $("exportJsonBtn").addEventListener("click", exportJson);
    $("themeBtn").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
    $("resetBtn").addEventListener("click", () => {
      applyConfigToControls(defaultConfig());
      runAnalysis();
    });

    $("scenario").addEventListener("change", applyScenarioPreset);
    $("nChannels").addEventListener("change", () => { renderChannelControls(null, true); updateControlLabels(); scheduleRun(); });
    $("nSamples").addEventListener("change", () => { syncRangeBounds(); scheduleRun(); });
    document.querySelector(".sidebar").addEventListener("input", (e) => {
      updateControlLabels();
      if (e.target.matches('input[type="range"]')) scheduleRun();
    });
    document.querySelector(".sidebar").addEventListener("change", (e) => {
      updateControlLabels();
      if (e.target.id === "scenario") return;
      if (e.target.name === "channelToggle") renderChannelControls(state.result);
      scheduleRun();
    });
    ["obsChannelSelect", "showObserved", "showRecon", "showNaive", "showResidual", "recalibrateEnabled"].forEach((id) => {
      $(id).addEventListener("change", drawObsChart);
    });
    let resizeTimer;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(renderCharts, 160);
    });
  }

  function init() {
    state.config = defaultConfig();
    applyConfigToControls(state.config);
    applyTheme(state.theme);
    initEvents();
    runAnalysis();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
