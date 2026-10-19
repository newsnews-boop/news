"use strict";

const DATA_URL = "data/news.json";
const KEEP_DAYS = 4;          // 既読・非表示の記録を残す日数
const STREAM_PAGE = 30;       // ニュース一覧を一度に表示する件数
const STORE = {
  prefs: "news.prefs", read: "news.read", visit: "news.visit",
  dismissed: "news.dismissed", tab: "news.tab",
};

let data = null;
let prefs = load(STORE.prefs, { ngWords: [], ngSources: [], hideRead: false, thumbs: true });
let read = load(STORE.read, {});
let dismissed = load(STORE.dismissed, {});
let tab = loadString(STORE.tab);
let topicsExpanded = false;
let streamLimit = STREAM_PAGE;
const visit = trackVisit();

// ---------------------------------------------------------------- 保存

function load(key, fallback) {
  try {
    const v = JSON.parse(localStorage.getItem(key));
    return v && typeof v === "object" ? { ...fallback, ...v } : fallback;
  } catch {
    return fallback;
  }
}

function loadString(key) {
  try { return localStorage.getItem(key) || ""; } catch { return ""; }
}

function save(key, value) {
  try {
    localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
  } catch { /* 保存できなくても表示は続ける */ }
}

// 30分以上あけて開いたら「前回の訪問」を更新し、それ以降の記事に NEW を付ける
function trackVisit() {
  const now = Date.now();
  const v = load(STORE.visit, { previous: 0, current: 0 });
  if (now - v.current > 30 * 60 * 1000) v.previous = v.current || now;
  v.current = now;
  save(STORE.visit, v);
  return v;
}

function prune(record, key) {
  const limit = Date.now() - KEEP_DAYS * 86400000;
  for (const [id, t] of Object.entries(record)) if (t < limit) delete record[id];
  save(key, record);
}

// ---------------------------------------------------------------- フィルタ

const norm = (s) => (s || "").normalize("NFKC").toLowerCase();

function isHidden(article) {
  const title = norm(article.title);
  const src = norm(article.source);
  return prefs.ngWords.some((w) => title.includes(norm(w))) ||
         prefs.ngSources.some((s) => src.includes(norm(s)));
}

// 代表記事が非表示対象なら、残った関連記事を代表に繰り上げる
function visibleCluster(item) {
  const members = [item, ...item.related].filter((a) => !isHidden(a));
  if (!members.length) return null;
  const [lead, ...related] = members;
  const same = lead === item;
  const headline = same && !isHidden({ title: item.headline, source: "" }) ? item.headline : lead.title;
  const summary = same && item.summary && !isHidden({ title: item.summary, source: "" }) ? item.summary : "";
  return {
    ...lead, key: item.id, headline, summary, related, members,
    image: same ? item.image : "",
    coverage: new Set(members.map((m) => m.source)).size,
  };
}

const isReadCluster = (c) => c.members.some((m) => read[m.id]);
const isNew = (c) => new Date(c.time) > visit.previous;

function collect(items, counter) {
  const out = [];
  for (const item of items) {
    if (dismissed[item.id]) continue;
    const c = visibleCluster(item);
    if (!c || (prefs.hideRead && isReadCluster(c))) counter.hidden++;
    else out.push(c);
  }
  return out;
}

// ---------------------------------------------------------------- 部品

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c != null && c !== "") e.append(c);
  return e;
}

const safeUrl = (u) => (/^https?:\/\//.test(u) ? u : "#");
const pad = (n) => String(n).padStart(2, "0");

function relTime(iso) {
  const t = new Date(iso);
  const min = Math.round((Date.now() - t) / 60000);
  if (min < 1) return "たった今";
  if (min < 60) return `${min}分前`;
  if (min < 24 * 60) return `${Math.floor(min / 60)}時間前`;
  return `${t.getMonth() + 1}/${t.getDate()} ${pad(t.getHours())}:${pad(t.getMinutes())}`;
}

function markRead(id) {
  read[id] = Date.now();
  save(STORE.read, read);
}

function articleLink(a, cls, onRead, ...children) {
  const mark = () => { markRead(a.id); onRead(); };
  return el("a", {
    class: cls, href: safeUrl(a.url), target: "_blank", rel: "noopener noreferrer",
    onclick: mark, onauxclick: mark,
  }, ...(children.length ? children : [a.title]));
}

// 報じた媒体の数を ▮ の数で示す（2社で1つ、最大4つ）
function coverageBar(n) {
  if (n < 2) return null;
  const filled = Math.min(4, n - 1);
  return el("span", { class: "bar", title: `${n}社が報道`, "aria-label": `${n}社が報道` },
    ...Array.from({ length: 4 }, (_, i) => el("i", { class: i < filled ? "on" : "" })));
}

function thumb(src, cls) {
  if (!src || !prefs.thumbs) return null;
  return el("img", {
    class: cls, src, alt: "", loading: "lazy", referrerpolicy: "no-referrer",
    onerror: (e) => e.target.remove(),
  });
}

const metaText = (c) => `${c.source} · ${relTime(c.time)}`;

// ---------------------------------------------------------------- トピックス（タブ）

function topicTabs(counter) {
  const [first, ...rest] = data.sections;
  const all = new Map(data.sections.flatMap((s) => s.items).map((i) => [i.id, i]));
  // 「主要」タブ：全ジャンルから選んだ話題 → 続けて主要ニュースのフィードの記事
  const hotIds = new Set(data.hot || []);
  const hotItems = [...(data.hot || []).map((id) => all.get(id)).filter(Boolean),
    ...first.items.filter((i) => !hotIds.has(i.id))];
  return [
    { id: first.id, name: first.name, items: collect(hotItems, counter) },
    ...rest.map((s) => ({ id: s.id, name: s.name, items: collect(s.items, counter) })),
  ];
}

// 見出し1行。押すとその場で概要などが開く
function renderTopicItem(c) {
  const li = el("li", { class: "item" });
  const refresh = () => {
    li.classList.toggle("read", isReadCluster(c));
    li.querySelectorAll(".related li").forEach((r) => r.classList.toggle("read", !!read[r.dataset.id]));
  };
  if (isNew(c)) li.classList.add("new");

  const detail = el("div", { class: "detail", hidden: true });
  const head = el("button", {
    class: "item-title", type: "button", "aria-expanded": "false",
    onclick: () => {
      const open = detail.hidden;
      if (open && !detail.childElementCount) fillDetail(detail, c, refresh);
      detail.hidden = !open;
      head.setAttribute("aria-expanded", String(open));
      li.classList.toggle("open", open);
    },
  }, c.headline, el("span", { class: "new-badge" }, "NEW"));

  li.append(el("div", { class: "item-main" }, head, coverageBar(c.coverage)), detail);
  refresh();
  return li;
}

function fillDetail(detail, c, refresh) {
  const sources = [...new Set(c.members.map((m) => m.source))];
  detail.append(
    el("div", { class: "detail-top" },
      el("div", { class: "detail-body" },
        c.headline !== c.title ? el("p", { class: "full-title" }, c.title) : null,
        c.summary ? el("p", { class: "summary" }, c.summary) : null,
        el("p", { class: "meta" }, metaText(c), c.coverage >= 2 ? ` · ${c.coverage}社が報道` : "")),
      thumb(c.image, "detail-img")),
    articleLink(c, "read-btn", refresh, "記事を読む →"));
  if (c.related.length) {
    detail.append(el("ul", { class: "related" }, ...c.related.map((r) =>
      el("li", { "data-id": r.id }, el("span", { class: "src" }, r.source), articleLink(r, "", refresh)))));
  }
  detail.append(el("div", { class: "mute-row" }, "この媒体を非表示：",
    ...sources.map((s) => el("button", { type: "button", onclick: () => muteSource(s) }, s))));
}

// 右側の写真：表示中の見出しのうち、写真のある最初の記事
function renderTopicPhoto(items) {
  const c = prefs.thumbs && items.find((i) => i.image);
  if (!c) return null;
  const fig = el("figure", { class: "topic-photo" });
  const refresh = () => fig.classList.toggle("read", isReadCluster(c));
  fig.append(articleLink(c, "topic-photo-link", refresh,
    thumb(c.image, "topic-photo-img"),
    el("figcaption", {}, el("span", { class: "topic-photo-title" }, c.headline),
      el("span", { class: "meta" }, metaText(c)))));
  refresh();
  return fig;
}

function renderTopics(counter) {
  const tabs = topicTabs(counter);
  const current = tabs.find((t) => t.id === tab) || tabs[0];
  const showN = data.show_per_section || 8;
  const shown = topicsExpanded ? current.items : current.items.slice(0, showN);

  const tabBar = el("div", { class: "tabs", role: "tablist" }, ...tabs.map((t) =>
    el("button", {
      class: "tab", type: "button", role: "tab", "aria-selected": String(t === current),
      onclick: () => {
        tab = t.id;
        save(STORE.tab, tab);
        topicsExpanded = false;
        render();
      },
    }, t.name)));

  const list = el("ol", { class: "topic-list" }, ...shown.map(renderTopicItem));
  if (!shown.length) list.append(el("li", { class: "empty" }, "表示できる記事がありません"));

  const more = current.items.length > showN ? el("button", {
    class: "show-more", type: "button",
    onclick: () => { topicsExpanded = !topicsExpanded; render(); },
  }, topicsExpanded ? "閉じる" : `もっと見る（${current.items.length - showN}件）`) : null;

  const gen = new Date(data.generated_at);
  return el("section", { class: "topics", "aria-label": "トピックス" },
    tabBar,
    el("div", { class: "topic-body" },
      el("div", { class: "topic-main" },
        el("p", { class: "updated" }, `${gen.getMonth() + 1}/${gen.getDate()} ${pad(gen.getHours())}:${pad(gen.getMinutes())} 更新`),
        list, more),
      renderTopicPhoto(shown.slice(0, showN))));
}

// ---------------------------------------------------------------- ランキング

const rankingsOpen = new Set();  // 「〜位を見る」を開いた一覧

const outLink = (url, cls, ...children) =>
  el("a", { class: cls, href: safeUrl(url), target: "_blank", rel: "noopener noreferrer" }, ...children);

function renderRankingGroup(r, g) {
  const key = `${r.id}:${g.name}`;
  const open = rankingsOpen.has(key);
  const show = r.show || 5;
  const items = open ? g.items : g.items.slice(0, show);
  return el("div", { class: "ranking-group" },
    g.name ? el("h3", { class: "ranking-group-head" }, g.name) : null,
    el("ol", { class: "ranking-list" }, ...items.map((x, n) =>
      el("li", {}, outLink(x.url, "",
        el("span", { class: "rank" }, String(n + 1)),
        thumb(x.image, "ranking-img"),
        el("span", { class: "ranking-text" },
          el("span", { class: "ranking-title" }, x.title),
          el("span", { class: "ranking-sub" }, x.sub)))))),
    el("div", { class: "ranking-actions" },
      g.items.length > show ? el("button", {
        class: "show-more", type: "button",
        onclick: () => { open ? rankingsOpen.delete(key) : rankingsOpen.add(key); render(); },
      }, open ? "閉じる" : `${show + 1}〜${g.items.length}位を見る`) : null,
      g.link ? outLink(g.link, "ranking-out", `${r.source}で見る →`) : null));
}

function renderRankings() {
  const lists = (data.rankings || []).filter((r) => (r.groups || []).some((g) => g.items.length));
  if (!lists.length) return null;
  return el("section", { class: "rankings", "aria-label": "ランキング" },
    ...lists.map((r) => el("div", { class: "ranking" },
      el("h2", { class: "ranking-head" }, `${r.name}ランキング`, el("small", {}, r.source)),
      ...r.groups.filter((g) => g.items.length).map((g) => renderRankingGroup(r, g)),
      r.links && r.links.length ? el("p", { class: "ranking-links" }, "もっと見る：",
        ...r.links.map((l) => outLink(l.url, "", `${l.name} →`))) : null)));
}

// ---------------------------------------------------------------- ニュース一覧（新しい順）

function renderCard(c) {
  const card = el("article", { class: "card" });
  const refresh = () => card.classList.toggle("read", isReadCluster(c));
  if (isNew(c)) card.classList.add("new");

  const img = thumb(c.image, "card-img");
  // 写真がない記事は、写真の代わりに概要を見せる
  const summary = !img && c.summary ? el("span", { class: "card-summary" }, c.summary) : null;
  card.classList.add(img ? "has-img" : summary ? "has-summary" : "text-only");
  if (img) img.addEventListener("error", () => card.classList.replace("has-img", "text-only"));
  card.append(
    articleLink(c, "card-link", refresh,
      img, summary,
      el("span", { class: "card-text" },
        el("span", { class: "card-title" }, c.headline),
        el("span", { class: "meta" }, coverageBar(c.coverage), ` ${metaText(c)}`))),
    el("button", {
      class: "dismiss", type: "button", "aria-label": "この記事を消す", title: "この記事を消す",
      onclick: () => dismiss(c, card),
    }, "×"));
  refresh();
  return card;
}

function renderStream(counter, exclude) {
  const seen = new Set(exclude);
  const all = [];
  for (const s of data.sections) {
    for (const c of collect(s.items, counter)) {
      if (!seen.has(c.key)) {
        seen.add(c.key);
        all.push(c);
      }
    }
  }
  all.sort((a, b) => new Date(b.time) - new Date(a.time));

  const grid = el("div", { class: "cards" }, ...all.slice(0, streamLimit).map(renderCard));
  const rest = all.length - streamLimit;
  return el("section", { class: "stream", "aria-label": "ニュース" },
    el("h2", { class: "stream-head" }, "ニュース", el("small", {}, "新しい順")),
    grid,
    rest > 0 ? el("button", {
      class: "show-more stream-more", type: "button",
      onclick: () => { streamLimit += STREAM_PAGE; render(); },
    }, `さらに表示（残り${rest}件）`) : null);
}

// ---------------------------------------------------------------- 全体

function render() {
  const main = document.getElementById("main");
  const scrollY = window.scrollY;
  const counter = { hidden: 0 };
  const topics = renderTopics(counter);
  // 主要タブの話題は一覧に重ねて出さない
  const stream = renderStream({ hidden: 0 }, data.hot || []);
  main.replaceChildren(...[topics, renderRankings(), stream].filter(Boolean));
  window.scrollTo(0, scrollY);

  const f = data.filtered || {};
  document.getElementById("footer-note").textContent =
    `共通フィルタで除外：NG媒体 ${f.ng_source ?? 0}件・NGワード ${f.ng_word ?? 0}件` +
    ` ／ この端末の設定で非表示：${counter.hidden}件`;
  renderFeedStatus();
}

function renderFeedStatus() {
  const ul = document.getElementById("feed-status");
  ul.replaceChildren(...data.feeds.map((f) => el("li", { class: f.ok ? "" : "ng" },
    `${f.ok ? "✓" : "×"} ${f.section} / ${f.source}：${f.ok ? `${f.count}件` : f.error}`)));
}

// ---------------------------------------------------------------- 操作

function muteSource(name) {
  if (!confirm(`「${name}」の記事を今後表示しないようにしますか？\n（設定からいつでも戻せます）`)) return;
  if (!prefs.ngSources.includes(name)) prefs.ngSources.push(name);
  save(STORE.prefs, prefs);
  render();
}

let toastTimer = 0;
function toast(...children) {
  const box = document.getElementById("toast");
  box.replaceChildren(...children);
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 6000);
}

function dismiss(c, card) {
  dismissed[c.key] = Date.now();
  save(STORE.dismissed, dismissed);
  card.remove();
  const close = () => { document.getElementById("toast").hidden = true; };
  toast(
    el("span", {}, "記事を消しました"),
    el("button", {
      type: "button",
      onclick: () => { delete dismissed[c.key]; save(STORE.dismissed, dismissed); close(); render(); },
    }, "元に戻す"),
    el("button", {
      type: "button",
      onclick: () => { close(); muteSource(c.source); },
    }, `「${c.source}」を今後表示しない`));
}

async function fetchData() {
  const btn = document.getElementById("reload");
  btn.classList.add("spinning");
  try {
    const res = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(res.status);
    data = await res.json();
    render();
  } catch (e) {
    if (!data) {
      document.getElementById("main").replaceChildren(
        el("p", { class: "status" }, "ニュースを読み込めませんでした。時間をおいて再読み込みしてください。"));
    }
  } finally {
    btn.classList.remove("spinning");
  }
}

const lines = (text) => text.split("\n").map((s) => s.trim()).filter(Boolean);

function setupSettings() {
  const dlg = document.getElementById("settings");
  const words = document.getElementById("ng-words");
  const sources = document.getElementById("ng-sources");
  const hideRead = document.getElementById("hide-read");
  const thumbs = document.getElementById("thumbs");

  document.getElementById("open-settings").addEventListener("click", () => {
    words.value = prefs.ngWords.join("\n");
    sources.value = prefs.ngSources.join("\n");
    hideRead.checked = prefs.hideRead;
    thumbs.checked = prefs.thumbs;
    dlg.returnValue = "";
    dlg.showModal();
  });
  dlg.addEventListener("close", () => {
    if (dlg.returnValue !== "save") return;
    prefs = {
      ngWords: lines(words.value), ngSources: lines(sources.value),
      hideRead: hideRead.checked, thumbs: thumbs.checked,
    };
    save(STORE.prefs, prefs);
    if (data) render();
  });
  document.getElementById("clear-read").addEventListener("click", () => {
    if (!confirm("既読と「×」で消した記事の記録をすべて消しますか？")) return;
    read = {};
    dismissed = {};
    save(STORE.read, read);
    save(STORE.dismissed, dismissed);
    if (data) render();
  });
  document.getElementById("export").addEventListener("click", async () => {
    const text = JSON.stringify({ ngWords: lines(words.value), ngSources: lines(sources.value) });
    try {
      await navigator.clipboard.writeText(text);
      alert("コピーしました。もう一方の端末の「読み込み」に貼り付けてください。");
    } catch {
      prompt("この文字列をコピーしてください", text);
    }
  });
  document.getElementById("import").addEventListener("click", () => {
    const text = prompt("書き出した文字列を貼り付けてください");
    if (!text) return;
    try {
      const v = JSON.parse(text);
      const merge = (a, b) => [...new Set([...lines(a), ...(Array.isArray(b) ? b : [])])].join("\n");
      words.value = merge(words.value, v.ngWords);
      sources.value = merge(sources.value, v.ngSources);
      alert("読み込みました。「保存」を押すと反映されます。");
    } catch {
      alert("形式が正しくありません。");
    }
  });
}

// ---------------------------------------------------------------- 起動

prune(read, STORE.read);
prune(dismissed, STORE.dismissed);
setupSettings();
document.getElementById("reload").addEventListener("click", fetchData);
// アプリを切り替えて戻ってきたとき、15分以上たっていれば最新を取りに行く
let lastFetch = Date.now();
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && Date.now() - lastFetch > 15 * 60 * 1000) {
    lastFetch = Date.now();
    fetchData();
  }
});
fetchData();
