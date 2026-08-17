  const form = document.getElementById('summarizeForm');
  const urlInput = document.getElementById('urlInput');
  const submitBtn = document.getElementById('submitBtn');
  const btnLabel = document.getElementById('btnLabel');
  const timelineWrap = document.getElementById('timelineWrap');
  const statusLine = document.getElementById('statusLine');
  const timelineFill = document.querySelector('.timeline .fill');
  const timelinePlayhead = document.querySelector('.timeline .playhead');
  const result = document.getElementById('result');
  const videoId = document.getElementById('videoId');
  const resultTitle = document.getElementById('resultTitle');
  const resultVideoDetails = document.getElementById('resultVideoDetails');
  const categoryPills = document.getElementById('categoryPills');
  const commentsBlockContainer = document.getElementById('commentsBlockContainer');
  const questionsPanel = document.getElementById('questionsPanel');
  const resultTags = document.getElementById('resultTags');
  const resultChannelDetails = document.getElementById('resultChannelDetails');
  const saveInsightsBox = document.getElementById('saveInsightsBox');
  const saveName = document.getElementById('saveName');
  const saveEmail = document.getElementById('saveEmail');
  const savePassword = document.getElementById('savePassword');
  const saveInsightsBtn = document.getElementById('saveInsightsBtn');
  const saveInsightsStatus = document.getElementById('saveInsightsStatus');
  let lastSummarizedUrl = null;
  // The prose summary isn't rendered anywhere anymore, so COPY reads it
  // from here instead of scraping it back out of the DOM.
  let lastSummaryText = '';
  const titleAnswer = document.getElementById('titleAnswer');
  const alreadySummarizedBanner = document.getElementById('alreadySummarizedBanner');
  const viewToggle = document.getElementById('viewToggle');
  const keyPointsList = document.getElementById('keyPointsList');
  const chaptersContainer = document.getElementById('chaptersContainer');
  const errorBox = document.getElementById('errorBox');
  const copyBtn = document.getElementById('copyBtn');

  // ---------- auth state ----------
  const authArea = document.getElementById('authArea');
  const authModal = document.getElementById('authModal');
  const authModalClose = document.getElementById('authModalClose');
  const authModalTitle = document.getElementById('authModalTitle');
  const authForm = document.getElementById('authForm');
  const authNameRow = document.getElementById('authNameRow');
  const authName = document.getElementById('authName');
  const authEmail = document.getElementById('authEmail');
  const authPassword = document.getElementById('authPassword');
  const authError = document.getElementById('authError');
  const authSubmitBtn = document.getElementById('authSubmitBtn');
  const authSwitchLink = document.getElementById('authSwitchLink');
  const authSwitch = document.getElementById('authSwitch');

  const mainView = document.getElementById('mainView');
  const historyView = document.getElementById('historyView');
  const searchFilter = document.getElementById('searchFilter');
  const dateFilter = document.getElementById('dateFilter');
  const categoryFilter = document.getElementById('categoryFilter');
  const sortFilter = document.getElementById('sortFilter');
  const clearFiltersBtn = document.getElementById('clearFiltersBtn');
  const categoryPanelBtn = document.getElementById('categoryPanelBtn');
  const categoryPanel = document.getElementById('categoryPanel');
  const categoryPanelList = document.getElementById('categoryPanelList');
  const historyList = document.getElementById('historyList');
  const historyEmpty = document.getElementById('historyEmpty');
  const historyTabs = document.querySelectorAll('.history-tab');

  const glossaryView = document.getElementById('glossaryView');
  const backToMainFromGlossaryBtn = document.getElementById('backToMainFromGlossaryBtn');
  const glossarySearch = document.getElementById('glossarySearch');
  const glossaryList = document.getElementById('glossaryList');
  const glossaryEmpty = document.getElementById('glossaryEmpty');
  const addTermToggleBtn = document.getElementById('addTermToggleBtn');
  const addTermBox = document.getElementById('addTermBox');
  const addTermInput = document.getElementById('addTermInput');
  const addTermStatus = document.getElementById('addTermStatus');
  const addTermCancelBtn = document.getElementById('addTermCancelBtn');
  const addTermConfirmBtn = document.getElementById('addTermConfirmBtn');
  let glossaryData = [];

  let currentStatusTab = 'unread';

  let currentUser = null;
  let authMode = 'login'; // 'login' | 'signup'

  // ---------- formatters ----------
  function formatDuration(seconds) {
    if (seconds == null) return null;
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    const mm = h > 0 ? String(m).padStart(2, '0') : String(m);
    const ss = String(s).padStart(2, '0');
    return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
  }

  function formatCount(n) {
    if (n == null) return null;
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, '') + 'K';
    return String(n);
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function keyPointsHtml(keyPoints, glossary) {
    return keyPoints.map((kp, i) => `
      <div class="key-point-row" data-idx="${i}">
        <div class="key-point-toggle" role="button" tabindex="0">
          <span class="key-point-arrow">▸</span>
          <span class="key-point-text">${linkifyGlossary(kp.point, glossary)}</span>
        </div>
        <div class="key-point-detail hidden">${linkifyGlossary(kp.detail, glossary)}</div>
      </div>
    `).join('');
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, '&quot;');
  }

  function escapeRegExp(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  // Wraps every occurrence of a glossary term in tappable markup so the
  // reader can get a definition inline instead of hunting the GLOSSARY
  // tab for it. Longest terms first so a multi-word term is matched
  // whole before a shorter term nested inside it grabs part of it.
  //
  // Finds every match in ONE pass over the original escaped text, then
  // builds the output from those spans — it must not do what an earlier
  // version did (sequential String.replace per term, each pass over the
  // cumulative HTML): once term A's definition/example text happened to
  // contain term B, term B's later pass matched inside term A's already-
  // inserted data-def/data-example attribute values, corrupting the
  // markup (broken attributes, definition text leaking onto the page).
  function linkifyGlossary(rawText, glossary) {
    // Glossary linking is a nice-to-have layered on top of the summary
    // text. It must never be able to take the summary itself down with
    // it — a throw in here used to bubble all the way out to the
    // submit handler's catch and render as "summarizing failed", hiding
    // a summary that had actually been generated and saved just fine.
    try {
      return linkifyGlossaryUnsafe(rawText, glossary);
    } catch (err) {
      console.error('glossary linkify failed, falling back to plain text', err);
      return escapeHtml(rawText || '');
    }
  }

  function linkifyGlossaryUnsafe(rawText, glossary) {
    const escaped = escapeHtml(rawText || '');
    const terms = (glossary || []).filter(g => g.term && g.term.trim());
    if (!terms.length) return escaped;
    const sorted = [...terms].sort((a, b) => b.term.length - a.term.length);
    const matches = [];
    for (const g of sorted) {
      const escTerm = escapeHtml(g.term.trim());
      if (!escTerm) continue;
      // Deliberately NOT using a lookbehind ((?<!...)) for the left-hand
      // boundary: WebKit only supports lookbehind from iOS 16.4 on, and
      // on anything older `new RegExp` throws outright ("The string did
      // not match the expected pattern"), which took down the whole
      // summary render. Match an optional leading boundary char as a
      // real capture group instead and skip past it — same result,
      // supported everywhere.
      const pattern = new RegExp(`(^|[^\\w>])(${escapeRegExp(escTerm)})(?![\\w])`, 'gi');
      let m;
      while ((m = pattern.exec(escaped))) {
        const start = m.index + m[1].length; // skip the boundary char we matched
        const end = start + m[2].length;
        if (matches.some(x => start < x.end && end > x.start)) continue; // overlaps a longer term already claimed
        matches.push({ start, end, term: g });
        // Overlapping matches can share a boundary char, so rewind the
        // scan position to just after the term itself rather than after
        // the whole match — otherwise a term immediately following
        // another gets skipped.
        pattern.lastIndex = end;
      }
    }
    if (!matches.length) return escaped;
    matches.sort((a, b) => a.start - b.start);
    let out = '';
    let cursor = 0;
    for (const m of matches) {
      out += escaped.slice(cursor, m.start);
      const text = escaped.slice(m.start, m.end);
      const escTerm = escapeHtml(m.term.term.trim());
      const defAttr = escapeAttr(m.term.definition || '');
      const exAttr = escapeAttr(m.term.example || '');
      // Deliberately a <span>, not a <button> — this can end up nested
      // inside the SHORT view's key-point-toggle <button>, and nested
      // <button>s are invalid HTML that browsers silently mangle.
      out += `<span class="gloss-term" role="button" tabindex="0" data-term="${escTerm}" data-def="${defAttr}" data-example="${exAttr}">${text}</span>`;
      cursor = m.end;
    }
    out += escaped.slice(cursor);
    return out;
  }

  // LONG view: same collapsible-bullet UX as SHORT instead of one dense
  // wall of text — a short bullet just shows in full, a long one shows a
  // truncated preview that expands to the full text on tap.
  const LONG_BLOCK_PREVIEW_LEN = 90;

  function longSummaryHtml(summaryText, glossary) {
    const lines = (summaryText || '').split('\n').map(l => l.trim()).filter(Boolean);
    const blocks = [];
    let lead = '';
    for (const line of lines) {
      const isBullet = /^[-*•]\s*/.test(line);
      const text = isBullet ? line.replace(/^[-*•]\s*/, '') : line;
      if (!lead && !isBullet && blocks.length === 0) {
        lead = text;
      } else {
        blocks.push(text);
      }
    }
    const leadHtml = lead ? `<div class="long-lead">${linkifyGlossary(lead, glossary)}</div>` : '';
    const blockRows = blocks.map((b, i) => {
      if (b.length <= LONG_BLOCK_PREVIEW_LEN) {
        return `<div class="long-block-static"><span class="key-point-text">${linkifyGlossary(b, glossary)}</span></div>`;
      }
      const preview = b.slice(0, LONG_BLOCK_PREVIEW_LEN - 1).trimEnd() + '…';
      return `
        <div class="key-point-row" data-idx="${i}">
          <div class="key-point-toggle" role="button" tabindex="0">
            <span class="key-point-arrow">▸</span>
            <span class="key-point-text">${escapeHtml(preview)}</span>
          </div>
          <div class="key-point-detail hidden">${linkifyGlossary(b, glossary)}</div>
        </div>
      `;
    }).join('');
    return `${leadHtml}${blockRows}`;
  }

  const glossPopover = document.getElementById('glossPopover');
  const glossPopoverClose = document.getElementById('glossPopoverClose');
  const glossPopoverTerm = document.getElementById('glossPopoverTerm');
  const glossPopoverDef = document.getElementById('glossPopoverDef');
  const glossPopoverExample = document.getElementById('glossPopoverExample');

  function closeGlossPopover() {
    glossPopover.classList.add('hidden');
  }

  glossPopoverClose.addEventListener('click', closeGlossPopover);
  glossPopover.addEventListener('click', (e) => {
    if (e.target === glossPopover) closeGlossPopover();
  });

  function openGlossPopoverFor(term) {
    glossPopoverTerm.textContent = term.dataset.term || '';
    glossPopoverDef.textContent = term.dataset.def || '';
    glossPopoverExample.textContent = term.dataset.example ? `"${term.dataset.example}"` : '';
    glossPopover.classList.remove('hidden');
  }

  document.addEventListener('click', (e) => {
    const term = e.target.closest('.gloss-term');
    if (!term) return;
    openGlossPopoverFor(term);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const target = e.target;
    if (!target.closest) return;
    const term = target.closest('.gloss-term');
    if (term) {
      e.preventDefault();
      openGlossPopoverFor(term);
      return;
    }
    // key-point-toggle and view-toggle-btn are plain divs (role="button"),
    // not real <button> elements — see the tap-highlight comment near
    // .key-point-toggle's CSS for why — so they don't get native
    // Enter/Space activation for free; wire it up manually for keyboard
    // users.
    const activatable = target.closest('.key-point-toggle, .view-toggle-btn');
    if (activatable) {
      e.preventDefault();
      activatable.click();
    }
  });

  // Delegated so it works for both the freshly-rendered result card and
  // any history card containing a key-points list.
  document.addEventListener('click', (e) => {
    if (e.target.closest('.gloss-term')) return; // let the glossary tap handler own this click
    const btn = e.target.closest('.key-point-toggle');
    if (!btn) return;
    const row = btn.closest('.key-point-row');
    const detail = row.querySelector('.key-point-detail');
    const open = row.classList.toggle('open');
    detail.classList.toggle('hidden', !open);
  });

  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.view-toggle-btn');
    if (!btn) return;
    const container = btn.closest('.summary-view-container');
    const isShort = btn.dataset.view === 'short';
    container.querySelectorAll('.view-toggle-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.view === btn.dataset.view);
    });
    const questions = container.querySelector('.summary-questions');
    if (questions) questions.classList.toggle('hidden', isShort);
    container.querySelector('.summary-short-content').classList.toggle('hidden', !isShort);

    // Self-heal: if the question set never loaded (offline at boot, a
    // failed request), fetch it now and fill the panel in rather than
    // leaving it stuck on "Loading…" with no way back.
    if (!isShort && questions && !QUESTIONS.length) {
      await loadQuestions();
      if (QUESTIONS.length) {
        const card = container.closest('.history-card');
        const id = card ? card.dataset.summaryId : (questions.querySelector('.questions-panel') || {}).dataset?.summaryId;
        if (id) questions.innerHTML = questionsHtml({ id, feedback: questionAnswersFor(id) });
      }
    }
  });

  // Answers already rendered into the DOM, so a re-render after a late
  // question-set load doesn't wipe selections the user already made.
  function questionAnswersFor(summaryId) {
    const panel = document.querySelector(`.questions-panel[data-summary-id="${CSS.escape(String(summaryId))}"]`);
    if (!panel) return null;
    const answers = {};
    panel.querySelectorAll('.q-option.selected').forEach(b => {
      answers[b.dataset.question] = b.dataset.value;
    });
    return Object.keys(answers).length ? answers : null;
  }

  function setSummaryView(mode) {
    const isShort = mode === 'short';
    viewToggle.querySelectorAll('.view-toggle-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.view === mode);
    });
    questionsPanel.classList.toggle('hidden', isShort);
    keyPointsList.classList.toggle('hidden', !isShort);
  }

  function watchUrl(item) {
    return `https://www.youtube.com/watch?v=${encodeURIComponent(item.video_id)}`;
  }

  // When the backend judged the real title to materially misrepresent the
  // video, it supplies an honest replacement. Show that as the heading and
  // keep the original visible underneath — never silently swap it, since
  // the reader still needs to recognise the video they clicked.
  function titleLinkHtml(item) {
    if (!item.title) return '';
    const display = item.true_title || item.title;
    const link = `<a href="${watchUrl(item)}" target="_blank" rel="noopener" onclick="return confirm('Open this video on YouTube?')">${escapeHtml(display)}</a>`;
    if (!item.true_title) return link;
    return `${link}<div class="original-title mono" title="The title on YouTube">
      <span class="renamed-badge">RENAMED</span> ${escapeHtml(item.title)}
    </div>`;
  }

  function videoDetailsHtml(item) {
    const rows = [];

    // Channel and Subscribers moved to their own CHANNEL DETAILS block —
    // this one is strictly about the video itself.
    if (item.published_at) {
      const d = new Date(item.published_at);
      if (!isNaN(d.getTime())) {
        rows.push(['Upload date', d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })]);
      }
    }
    const duration = formatDuration(item.duration_seconds);
    if (duration) rows.push(['Duration', duration]);
    const views = formatCount(item.view_count);
    if (views != null) rows.push(['Views', views]);
    const likes = formatCount(item.like_count);
    if (likes != null) rows.push(['Likes', likes]);
    const comments = formatCount(item.comment_count);
    if (comments != null) rows.push(['Comments', comments]);

    if (rows.length === 0) return '';

    return `<details class="video-details">
      <summary class="video-details-toggle mono">▸ VIDEO DETAILS</summary>
      <div class="video-details-body">
        ${detailRows(rows)}
      </div>
    </details>`;
  }

  function detailRows(rows) {
    return rows.map(([label, value]) =>
      `<div class="detail-row"><span class="detail-label mono">${label}</span><span class="detail-value">${value}</span></div>`
    ).join('');
  }

  // Same shape as VIDEO DETAILS, but about the channel behind the video —
  // useful for judging whether a source is worth following, which the
  // per-video numbers alone don't tell you.
  function channelDetailsHtml(item) {
    const cs = item.channel_stats || {};
    const rows = [];

    if (item.channel) {
      const channelText = escapeHtml(item.channel);
      rows.push(['Channel', item.channel_id
        ? `<a href="https://www.youtube.com/channel/${encodeURIComponent(item.channel_id)}" target="_blank" rel="noopener">${channelText}</a>`
        : channelText]);
    }
    const subs = formatCount(item.subscriber_count ?? cs.subscriber_count);
    if (subs != null) rows.push(['Subscribers', subs]);
    const totalViews = formatCount(cs.total_views);
    if (totalViews != null) rows.push(['Total views', totalViews]);
    const videoCount = formatCount(cs.video_count);
    if (videoCount != null) rows.push(['Videos', videoCount]);
    const avgViews = formatCount(cs.avg_views_per_video);
    if (avgViews != null) rows.push(['Avg views / video', avgViews]);
    const avgLikes = formatCount(cs.avg_likes_per_video);
    if (avgLikes != null) {
      // Lifetime average likes isn't available from the API, so this is a
      // recent-uploads sample — say so rather than implying it's all-time.
      const note = cs.avg_likes_sample ? ` <span class="detail-note mono">last ${cs.avg_likes_sample}</span>` : '';
      rows.push(['Avg likes / video', `${avgLikes}${note}`]);
    }

    if (rows.length === 0) return '';

    return `<details class="video-details">
      <summary class="video-details-toggle mono">▸ CHANNEL DETAILS</summary>
      <div class="video-details-body">
        ${detailRows(rows)}
      </div>
    </details>`;
  }

  // ---------- questions ----------
  // Fetched rather than hardcoded so the server owns the question set and
  // the two can't drift apart.
  let QUESTIONS = [];

  async function loadQuestions() {
    try {
      const res = await fetch('/questions');
      if (res.ok) QUESTIONS = await res.json();
    } catch { /* questions panel just stays empty */ }
  }

  function questionsHtml(item) {
    if (!QUESTIONS.length) return '<div class="questions-empty mono">Loading…</div>';
    const answers = item.feedback || {};
    const rows = QUESTIONS.map(q => {
      const chosen = answers[q.key];
      const opts = q.options.map(o =>
        `<button type="button" class="q-option mono${chosen === o.value ? ' selected' : ''}"
                 data-question="${escapeHtml(q.key)}" data-value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</button>`
      ).join('');
      return `<div class="q-row"><div class="q-prompt">${escapeHtml(q.prompt)}</div><div class="q-options">${opts}</div></div>`;
    }).join('');
    const answered = QUESTIONS.filter(q => answers[q.key]).length;
    return `<div class="questions-panel" data-summary-id="${item.id}">
      ${rows}
      <div class="q-progress mono">${answered} of ${QUESTIONS.length} answered${answered ? ' · tap again to clear' : ''}</div>
    </div>`;
  }

  // Delegated so it covers every card plus the fresh-result view.
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.q-option');
    if (!btn) return;
    const panel = btn.closest('.questions-panel');
    if (!panel) return;
    const summaryId = panel.dataset.summaryId;
    if (!summaryId) return;

    const key = btn.dataset.question;
    const wasSelected = btn.classList.contains('selected');
    // Tapping the chosen answer again clears it — an accidental tap
    // shouldn't be permanent in a dataset meant to reflect real opinion.
    const value = wasSelected ? '' : btn.dataset.value;

    // Optimistic: this is a one-tap interaction, waiting on the network
    // before showing the selection would feel broken.
    panel.querySelectorAll(`.q-option[data-question="${CSS.escape(key)}"]`)
      .forEach(b => b.classList.remove('selected'));
    if (value) btn.classList.add('selected');
    updateQuestionProgress(panel);

    try {
      const res = await fetch(`/history/${summaryId}/feedback`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answers: { [key]: value } }),
      });
      if (!res.ok) throw new Error();
    } catch {
      // Put the UI back rather than leaving a selection that never saved.
      panel.querySelectorAll(`.q-option[data-question="${CSS.escape(key)}"]`)
        .forEach(b => b.classList.remove('selected'));
      if (wasSelected) btn.classList.add('selected');
      updateQuestionProgress(panel);
      showToast('Couldn’t save that answer', true);
    }
  });

  function updateQuestionProgress(panel) {
    const el = panel.querySelector('.q-progress');
    if (!el) return;
    const answered = panel.querySelectorAll('.q-option.selected').length;
    el.textContent = `${answered} of ${QUESTIONS.length} answered${answered ? ' · tap again to clear' : ''}`;
  }

  // Fine-grained topic tags, distinct from the coarse category pills.
  function tagsHtml(item) {
    if (!item.tags || !item.tags.length) return '';
    return `<div class="tag-row">${item.tags.map(t => `<span class="topic-tag mono">${escapeHtml(t)}</span>`).join('')}</div>`;
  }

  function categoryPillsHtml(category) {
    if (!category) return '';
    return category.split(',').map(c => c.trim()).filter(Boolean)
      .map(c => `<span class="category-pill mono">${escapeHtml(c)}</span>`).join('');
  }

  function chaptersHtml(item) {
    if (!item.chapters || item.chapters.length === 0) return '';
    const links = item.chapters.map(ch => {
      const url = `${watchUrl(item)}&t=${ch.seconds}s`;
      return `<a class="chapter-link" href="${url}" target="_blank" rel="noopener" onclick="return confirm('Open this video on YouTube?')">
        <span class="chapter-time mono">${formatDuration(ch.seconds)}</span>
        <span class="chapter-divider"></span>
        <span>${escapeHtml(ch.label)}</span>
      </a>`;
    }).join('');
    return `<details class="chapters-list">
      <summary class="chapters-heading mono">Sections (${item.chapters.length})</summary>
      ${links}
    </details>`;
  }

  // Counts and like totals per sentiment. The model classified each
  // comment; the arithmetic was done server-side from YouTube's real
  // like counts, so these are actual numbers, not an LLM's estimate.
  function commentTallyHtml(item) {
    const t = item.comment_tally;
    if (!t) return '';
    const rows = [
      ['positive', 'Positive', 'tally-positive'],
      ['negative', 'Negative', 'tally-negative'],
      ['neutral', 'Neutral', 'tally-neutral'],
    ].filter(([key]) => t[key] && t[key].count > 0);
    if (!rows.length) return '';

    const maxCount = Math.max(...rows.map(([key]) => t[key].count));
    const body = rows.map(([key, label, cls]) => {
      const { count, likes } = t[key];
      const pct = maxCount ? Math.round((count / maxCount) * 100) : 0;
      return `<div class="tally-row">
        <span class="tally-label mono ${cls}">${label}</span>
        <span class="tally-bar"><span class="tally-bar-fill ${cls}" style="width:${pct}%"></span></span>
        <span class="tally-nums mono">${count} · ${formatCount(likes) || 0} likes</span>
      </div>`;
    }).join('');

    return `<div class="comment-tally">
      <div class="comment-tally-title mono">Comment breakdown <span class="tally-sample">${t.total_classified} of top ${t.total_comments_sampled}</span></div>
      ${body}
    </div>`;
  }

  // One consolidated block for everything comment-derived. This used to be
  // four separate stacked sections (blurb, tally, upside, other side), each
  // with its own padding and heading, which ate a lot of vertical space for
  // what is really one topic.
  function commentsBlockHtml(item) {
    const tally = commentTallyHtml(item);
    const hasVerdict = item.sentiment_label || item.sentiment_blurb;
    if (!tally && !hasVerdict && !item.highlight && !item.counterpoint) return '';

    const verdict = hasVerdict
      ? `<div class="comment-verdict">
          ${item.sentiment_label ? sentimentPillHtml(item) : ''}
          ${item.sentiment_blurb ? `<span class="comment-verdict-text">${escapeHtml(item.sentiment_blurb)}</span>` : ''}
        </div>`
      : '';

    const takes = [
      item.highlight ? ['+', 'take-up', item.highlight] : null,
      item.counterpoint ? ['−', 'take-down', item.counterpoint] : null,
    ].filter(Boolean).map(([sign, cls, text]) =>
      `<div class="comment-take ${cls}"><span class="take-sign mono">${sign}</span><span>${escapeHtml(text)}</span></div>`
    ).join('');

    return `<div class="comments-block">${verdict}${tally}${takes}</div>`;
  }

  // ---------- toast ----------
  let toastTimer = null;

  function showToast(message, isError) {
    let el = document.getElementById('toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'toast';
      el.className = 'toast mono';
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.toggle('toast-error', !!isError);
    el.classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('visible'), 3200);
  }

  // ---------- returning-from-another-app state ----------
  // Leaving for Obsidian (or any app switch) can cause the browser to
  // re-run the page, which would otherwise drop you back at the top of a
  // fully-collapsed library. Remember scroll position and which cards
  // were open, and put it all back.
  const SCROLL_STATE_KEY = 'tldw_scroll_state';

  function saveScrollState() {
    try {
      const expanded = [...document.querySelectorAll('.history-card.expanded')]
        .map(c => c.dataset.summaryId)
        .filter(Boolean);
      sessionStorage.setItem(SCROLL_STATE_KEY, JSON.stringify({
        y: window.scrollY,
        expanded,
        at: Date.now(),
      }));
    } catch { /* private browsing — just lose the position */ }
  }

  function restoreScrollState() {
    let state = null;
    try {
      const raw = sessionStorage.getItem(SCROLL_STATE_KEY);
      if (raw) state = JSON.parse(raw);
    } catch { return; }
    if (!state) return;
    // Only meaningful for a quick round-trip; an hour later the user has
    // moved on and being teleported mid-list would be confusing.
    if (Date.now() - (state.at || 0) > 10 * 60 * 1000) return;

    for (const id of state.expanded || []) {
      const card = document.querySelector(`.history-card[data-summary-id="${id}"]`);
      if (!card) continue;
      card.classList.add('expanded');
      const btn = card.querySelector('.history-card-toggle');
      if (btn) btn.textContent = 'COLLAPSE ▴';
    }
    if (typeof state.y === 'number') window.scrollTo(0, state.y);
  }

  // Any backgrounding counts, not just the Obsidian hand-off — iOS is
  // free to discard and re-run the page whenever it's not in front.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') saveScrollState();
  });

  // ---------- Obsidian export ----------
  // Vault name lives in localStorage rather than the account: it's a
  // property of this device's Obsidian install, and asking the server to
  // store it would mean a schema change for something one prompt covers.
  const OBSIDIAN_VAULT_KEY = 'tldw_obsidian_vault';
  const OBSIDIAN_FOLDER_KEY = 'tldw_obsidian_folder';

  function getObsidianVault() {
    try { return localStorage.getItem(OBSIDIAN_VAULT_KEY) || ''; } catch { return ''; }
  }

  // A sensible default so notes land somewhere tidy instead of the vault
  // root. Only applies until the user saves their own choice — saving an
  // empty folder is respected and files notes at the root.
  const OBSIDIAN_DEFAULT_FOLDER = 'Summaries';

  function getObsidianFolder() {
    try {
      const stored = localStorage.getItem(OBSIDIAN_FOLDER_KEY);
      return stored === null ? OBSIDIAN_DEFAULT_FOLDER : stored;
    } catch { return OBSIDIAN_DEFAULT_FOLDER; }
  }

  // Obsidian filenames can't contain these, and a stray one silently
  // breaks the whole obsidian:// call rather than erroring usefully.
  function safeNoteName(title, videoId) {
    const base = (title || videoId || 'Untitled').replace(/[\\/:*?"<>|#^[\]]/g, '').trim();
    return (base.slice(0, 100) || videoId || 'Untitled');
  }

  // Characters Obsidian treats specially inside [[...]] — a stray pipe
  // would silently turn the rest of the term into a display alias, and
  // brackets/hash/caret break the link outright.
  function wikiSafe(term) {
    return String(term || '').replace(/[[\]|#^]/g, '').trim() || 'Untitled';
  }

  function yamlEscape(s) {
    return '"' + String(s || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
  }

  function summaryToMarkdown(item) {
    const url = watchUrl(item);
    const cats = (item.category || '').split(',').map(c => c.trim()).filter(Boolean);
    // Obsidian tags can't contain spaces or '&'.
    const tags = cats.map(c => c.toLowerCase().replace(/&/g, 'and').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''));

    // Prefer the honest title for the note itself — that's the whole
    // point of it — but keep the real YouTube title in frontmatter so the
    // note is still traceable to the video you actually watched.
    const displayTitle = item.true_title || item.title || item.video_id;

    const lines = [];
    lines.push('---');
    lines.push(`title: ${yamlEscape(displayTitle)}`);
    if (item.true_title && item.title) lines.push(`original_title: ${yamlEscape(item.title)}`);
    lines.push(`source: ${url}`);
    if (item.channel) lines.push(`channel: ${yamlEscape(item.channel)}`);
    if (item.published_at) lines.push(`published: ${item.published_at.slice(0, 10)}`);
    lines.push(`summarized: ${new Date(item.created_at || Date.now()).toISOString().slice(0, 10)}`);
    if (item.duration_seconds) lines.push(`duration: ${formatDuration(item.duration_seconds)}`);
    lines.push(`tags: [tldw${tags.length ? ', ' + tags.join(', ') : ''}]`);
    lines.push('---');
    lines.push('');
    lines.push(`# ${displayTitle}`);
    lines.push('');
    lines.push(`[Watch on YouTube](${url})`);
    lines.push('');

    if (item.title_answer) {
      lines.push(`> [!tldr] The gist`);
      lines.push(`> ${item.title_answer}`);
      lines.push('');
    }

    if (item.key_points && item.key_points.length) {
      lines.push('## Key points');
      lines.push('');
      for (const kp of item.key_points) {
        lines.push(`- **${kp.point}**`);
        if (kp.detail && kp.detail !== kp.point) lines.push(`    - ${kp.detail}`);
      }
      lines.push('');
    }

    if (item.summary) {
      lines.push('## Summary');
      lines.push('');
      lines.push(item.summary);
      lines.push('');
    }

    if (item.chapters && item.chapters.length) {
      lines.push('## Sections');
      lines.push('');
      for (const ch of item.chapters) {
        lines.push(`- [${formatDuration(ch.seconds)}](${url}&t=${ch.seconds}s) — ${ch.label}`);
      }
      lines.push('');
    }

    if (item.glossary && item.glossary.length) {
      lines.push('## Glossary');
      lines.push('');
      for (const g of item.glossary) {
        // Wikilinks, so every video mentioning a term links to the same
        // node and Obsidian's graph shows which concepts cluster across
        // the library — the thing a plain-text export can't give you.
        lines.push(`- [[${wikiSafe(g.term)}]] — ${g.definition}`);
      }
      lines.push('');
    }

    const t = item.comment_tally;
    if (item.sentiment_label || t || item.highlight || item.counterpoint) {
      lines.push('## What the comments say');
      lines.push('');
      if (item.sentiment_label) {
        lines.push(`**Overall:** ${item.sentiment_label}${item.sentiment_blurb ? ' — ' + item.sentiment_blurb : ''}`);
        lines.push('');
      }
      if (t) {
        lines.push('| Sentiment | Comments | Likes |');
        lines.push('| --- | ---: | ---: |');
        for (const key of ['positive', 'negative', 'neutral']) {
          if (t[key] && t[key].count > 0) {
            lines.push(`| ${key[0].toUpperCase() + key.slice(1)} | ${t[key].count} | ${t[key].likes} |`);
          }
        }
        lines.push('');
      }
      if (item.highlight) { lines.push(`**The upside:** ${item.highlight}`); lines.push(''); }
      if (item.counterpoint) { lines.push(`**The other side:** ${item.counterpoint}`); lines.push(''); }
    }

    lines.push('---');
    lines.push('*Summarized by [TLDW](https://www.toolazydidntwatch.com)*');
    return lines.join('\n');
  }

  async function copyMarkdown(item) {
    const md = summaryToMarkdown(item);
    try {
      await navigator.clipboard.writeText(md);
      showToast('Markdown copied — paste into Obsidian');
    } catch {
      showToast('Couldn’t copy automatically', true);
    }
  }

  const obsidianModal = document.getElementById('obsidianModal');
  const obsidianModalClose = document.getElementById('obsidianModalClose');
  const obsidianVaultInput = document.getElementById('obsidianVault');
  const obsidianFolderInput = document.getElementById('obsidianFolder');
  const obsidianStatus = document.getElementById('obsidianStatus');
  const obsidianSaveBtn = document.getElementById('obsidianSaveBtn');
  let pendingObsidianItem = null;

  obsidianModalClose.addEventListener('click', () => closeObsidianModal());
  obsidianModal.addEventListener('click', (e) => {
    if (e.target === obsidianModal) closeObsidianModal();
  });

  // Reflects the saved destination in the Actions panel so it's visible
  // without opening the modal.
  function renderObsidianCurrent() {
    const el = document.getElementById('obsidianCurrent');
    if (!el) return;
    const vault = getObsidianVault();
    if (!vault) {
      el.textContent = 'Not set up yet — where SEND TO OBSIDIAN files your notes.';
      return;
    }
    const folder = getObsidianFolder();
    el.textContent = `Saving to ${vault}${folder ? ' / ' + folder : ' (vault root)'}`;
  }

  obsidianSaveBtn.addEventListener('click', () => {
    const vault = obsidianVaultInput.value.trim();
    if (!vault) {
      obsidianStatus.textContent = 'Vault name is required.';
      return;
    }
    try {
      localStorage.setItem(OBSIDIAN_VAULT_KEY, vault);
      localStorage.setItem(OBSIDIAN_FOLDER_KEY, obsidianFolderInput.value.trim());
    } catch { /* private browsing — the send below still works this once */ }
    const item = pendingObsidianItem;
    closeObsidianModal();
    renderObsidianCurrent();
    if (item) sendToObsidian(item);
    else showToast(`Notes will save to ${vault}${obsidianFolderInput.value.trim() ? ' / ' + obsidianFolderInput.value.trim() : ''}`);
  });

  function openObsidianSetup(item) {
    pendingObsidianItem = item || null;
    obsidianVaultInput.value = getObsidianVault();
    obsidianFolderInput.value = getObsidianFolder();
    obsidianModal.classList.remove('hidden');
  }

  function closeObsidianModal() {
    obsidianModal.classList.add('hidden');
    pendingObsidianItem = null;
  }

  // Keep well under what a custom-scheme URL can carry. A real summary is
  // ~8.5KB of markdown and URL-encoding inflates that by ~1.4x, so the
  // inline-content path only ever handles genuinely short notes; anything
  // bigger goes via the clipboard route below, which has no size ceiling.
  const OBSIDIAN_INLINE_LIMIT = 3000;

  function sendToObsidian(item) {
    const vault = getObsidianVault();
    if (!vault) {
      openObsidianSetup(item);
      return;
    }
    const md = summaryToMarkdown(item);
    const folder = getObsidianFolder().replace(/^\/+|\/+$/g, '');
    const name = safeNoteName(item.true_title || item.title, item.video_id);
    const filePath = folder ? `${folder}/${name}` : name;
    // silent=true tells Obsidian to file the note without opening it in
    // the editor. Switching apps at all is the OS handling the
    // obsidian:// scheme and can't be avoided from a web page, but this
    // at least means you land back where you were instead of staring at
    // a freshly-opened note you didn't ask to read.
    //
    // x-success is the x-callback-url convention for "come back here when
    // you're done". Obsidian honours it on some versions and ignores it on
    // others — it's a harmless unknown query param either way, so it costs
    // nothing to ask. When it does fire we land back on the Library with
    // ?from=obsidian and confirm the save. When it doesn't, iOS still shows
    // its own back-chip to return in one tap.
    const returnUrl = `${window.location.origin}/?from=obsidian`;
    const base = `obsidian://new?vault=${encodeURIComponent(vault)}`
      + `&file=${encodeURIComponent(filePath)}`
      + `&silent=true`
      + `&x-success=${encodeURIComponent(returnUrl)}`;

    // Snapshot where the user is so returning from Obsidian doesn't dump
    // them at the top of a collapsed list — the app switch can cause the
    // browser to re-run the page from scratch.
    saveScrollState();
    const inlineUri = `${base}&content=${encodeURIComponent(md)}`;

    // Short enough to hand over directly, which leaves the clipboard alone.
    if (inlineUri.length <= OBSIDIAN_INLINE_LIMIT) {
      window.location.href = inlineUri;
      return;
    }

    // Otherwise use Obsidian's own clipboard parameter: it reads the note
    // body from the clipboard rather than the URL, so note length stops
    // mattering entirely. writeText is called before any await so it still
    // counts as happening inside the tap — Safari rejects clipboard writes
    // that lose their user-gesture context.
    navigator.clipboard.writeText(md).then(() => {
      window.location.href = `${base}&clipboard=true`;
      showToast('Opening Obsidian…');
    }).catch(() => {
      showToast('Couldn’t reach the clipboard — try COPY MARKDOWN and paste it in', true);
    });
  }

  function sentimentPillHtml(item) {
    if (!item.sentiment_label) return '';
    const label = item.sentiment_label.toLowerCase();
    let cls = 'sentiment-mixed';
    if (label.includes('positive')) cls = 'sentiment-positive';
    else if (label.includes('negative')) cls = 'sentiment-negative';
    return `<span class="sentiment-pill mono ${cls}">${escapeHtml(item.sentiment_label)}</span>`;
  }

  function renderAuthArea() {
    authArea.innerHTML = '';
    if (currentUser) {
      const libraryBtn = document.createElement('button');
      libraryBtn.textContent = 'LIBRARY';
      libraryBtn.addEventListener('click', showHistoryView);

      const summarizeBtn = document.createElement('button');
      summarizeBtn.textContent = 'SUMMARIZE';
      summarizeBtn.addEventListener('click', showMainView);

      const glossaryBtn = document.createElement('button');
      glossaryBtn.textContent = 'GLOSSARY';
      glossaryBtn.addEventListener('click', showGlossaryView);

      const queueBtn = document.createElement('button');
      queueBtn.textContent = 'QUEUE';
      queueBtn.addEventListener('click', showQueueView);

      // Doubles as the profile entry point (name/email/password,
      // preferences, tokens, export, danger zone) — the avatar button
      // used to open the same modal, so it was pure duplication.
      const actionsBtn = document.createElement('button');
      actionsBtn.textContent = 'ACTIONS';
      actionsBtn.title = currentUser.full_name || currentUser.email;
      actionsBtn.addEventListener('click', openActionsModal);

      authArea.append(libraryBtn, queueBtn, summarizeBtn, glossaryBtn, actionsBtn);
    } else {
      const signInBtn = document.createElement('button');
      signInBtn.textContent = 'SIGN IN';
      signInBtn.addEventListener('click', () => openAuthModal('login'));

      const signUpBtn = document.createElement('button');
      signUpBtn.textContent = 'SIGN UP';
      signUpBtn.addEventListener('click', () => openAuthModal('signup'));

      authArea.append(signInBtn, signUpBtn);
    }
  }

  async function refreshAuthState() {
    try {
      const res = await fetch('/auth/me');
      const data = await res.json();
      currentUser = data || null;
    } catch {
      currentUser = null;
    }
    renderAuthArea();
  }

  function openAuthModal(mode) {
    authMode = mode;
    authError.textContent = '';
    authForm.reset();
    if (mode === 'login') {
      authModalTitle.textContent = 'Sign in';
      authSubmitBtn.textContent = 'SIGN IN';
      authSwitch.innerHTML = 'No account? <a href="#" id="authSwitchLink">Create one</a>';
      authNameRow.classList.add('hidden');
      authName.required = false;
    } else {
      authModalTitle.textContent = 'Create account';
      authSubmitBtn.textContent = 'SIGN UP';
      authSwitch.innerHTML = 'Already have an account? <a href="#" id="authSwitchLink">Sign in</a>';
      authNameRow.classList.remove('hidden');
      authName.required = true;
    }
    document.getElementById('authSwitchLink').addEventListener('click', (e) => {
      e.preventDefault();
      openAuthModal(mode === 'login' ? 'signup' : 'login');
    });
    showAuthScreen('authFormScreen');
    authModal.classList.remove('hidden');
    authEmail.focus();
  }

  function closeAuthModal() {
    authModal.classList.add('hidden');
  }

  // ---------- forgot password ----------
  const authFormScreen = document.getElementById('authFormScreen');
  const forgotEmailScreen = document.getElementById('forgotEmailScreen');
  const resetCodeScreen = document.getElementById('resetCodeScreen');
  const forgotPasswordLink = document.getElementById('forgotPasswordLink');
  const backToSignInLink = document.getElementById('backToSignInLink');
  const forgotEmailForm = document.getElementById('forgotEmailForm');
  const forgotEmail = document.getElementById('forgotEmail');
  const forgotError = document.getElementById('forgotError');
  const resetCodeForm = document.getElementById('resetCodeForm');
  const resetCode = document.getElementById('resetCode');
  const resetNewPassword = document.getElementById('resetNewPassword');
  const resetError = document.getElementById('resetError');
  const resetHint = document.getElementById('resetHint');

  function showAuthScreen(id) {
    [authFormScreen, forgotEmailScreen, resetCodeScreen].forEach(el => {
      el.classList.toggle('hidden', el.id !== id);
    });
  }

  forgotPasswordLink.addEventListener('click', (e) => {
    e.preventDefault();
    forgotError.textContent = '';
    forgotEmail.value = authEmail.value || '';
    showAuthScreen('forgotEmailScreen');
  });

  backToSignInLink.addEventListener('click', (e) => {
    e.preventDefault();
    showAuthScreen('authFormScreen');
  });

  forgotEmailForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    forgotError.textContent = '';
    try {
      const res = await fetch('/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: forgotEmail.value.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Something went wrong.');
      }
      resetError.textContent = '';
      resetCode.value = '';
      resetNewPassword.value = '';
      resetHint.textContent = `If ${forgotEmail.value.trim()} has an account, a code is on its way — enter it below.`;
      showAuthScreen('resetCodeScreen');
    } catch (err) {
      forgotError.textContent = err.message || 'Something went wrong.';
    }
  });

  resetCodeForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    resetError.textContent = '';
    try {
      const res = await fetch('/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: forgotEmail.value.trim(),
          code: resetCode.value.trim(),
          new_password: resetNewPassword.value,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        const detail = Array.isArray(data.detail)
          ? data.detail.map(d => d.msg).join(' ')
          : (data.detail || 'Something went wrong.');
        throw new Error(detail);
      }
      openAuthModal('login');
      authEmail.value = forgotEmail.value.trim();
      authError.textContent = 'Password reset — sign in with your new password.';
    } catch (err) {
      resetError.textContent = err.message || 'Something went wrong.';
    }
  });

  authModalClose.addEventListener('click', closeAuthModal);
  authModal.addEventListener('click', (e) => {
    if (e.target === authModal) closeAuthModal();
  });

  authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    authError.textContent = '';
    const endpoint = authMode === 'login' ? '/auth/login' : '/auth/signup';
    const body = { email: authEmail.value.trim(), password: authPassword.value };
    if (authMode === 'signup') body.full_name = authName.value.trim();
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        const detail = Array.isArray(data.detail)
          ? data.detail.map(d => d.msg).join(' ')
          : (data.detail || 'Something went wrong.');
        throw new Error(detail);
      }
      currentUser = data;
      renderAuthArea();
      closeAuthModal();
    } catch (err) {
      authError.textContent = err.message || 'Something went wrong.';
    }
  });

  async function logout() {
    await fetch('/auth/logout', { method: 'POST' });
    currentUser = null;
    renderAuthArea();
    showMainView();
  }

  // ---------- api tokens ----------
  const tokenModal = document.getElementById('tokenModal');
  const tokenModalClose = document.getElementById('tokenModalClose');
  const newTokenBox = document.getElementById('newTokenBox');
  const newTokenValue = document.getElementById('newTokenValue');
  const copyTokenBtn = document.getElementById('copyTokenBtn');
  const createTokenForm = document.getElementById('createTokenForm');
  const tokenLabelInput = document.getElementById('tokenLabelInput');
  const tokenList = document.getElementById('tokenList');
  const tokenEmpty = document.getElementById('tokenEmpty');

  function openTokenModal() {
    newTokenBox.classList.add('hidden');
    tokenModal.classList.remove('hidden');
    loadTokens();
  }

  function closeTokenModal() {
    tokenModal.classList.add('hidden');
  }

  tokenModalClose.addEventListener('click', closeTokenModal);
  tokenModal.addEventListener('click', (e) => {
    if (e.target === tokenModal) closeTokenModal();
  });

  copyTokenBtn.addEventListener('click', async () => {
    await navigator.clipboard.writeText(newTokenValue.textContent);
    copyTokenBtn.textContent = 'COPIED';
    setTimeout(() => (copyTokenBtn.textContent = 'COPY'), 1500);
  });

  createTokenForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const res = await fetch('/auth/tokens', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ label: tokenLabelInput.value.trim() }),
      });
      if (!res.ok) return;
      const data = await res.json();
      newTokenValue.textContent = data.token;
      newTokenBox.classList.remove('hidden');
      tokenLabelInput.value = '';
      loadTokens();
    } catch { /* ignore */ }
  });

  async function loadTokens() {
    tokenList.innerHTML = '';
    tokenEmpty.classList.remove('visible');
    try {
      const res = await fetch('/auth/tokens');
      if (!res.ok) return;
      const tokens = await res.json();
      if (tokens.length === 0) {
        tokenEmpty.classList.add('visible');
        return;
      }
      for (const t of tokens) {
        const row = document.createElement('div');
        row.className = 'token-row';
        const created = new Date(t.created_at).toLocaleDateString();
        const lastUsed = t.last_used_at ? new Date(t.last_used_at).toLocaleDateString() : 'never';
        row.innerHTML = `
          <div>
            <div>${t.label || '(unlabeled)'}</div>
            <div class="token-row-meta mono">created ${created} · last used ${lastUsed}</div>
          </div>
        `;
        const revokeBtn = document.createElement('button');
        revokeBtn.className = 'token-revoke';
        revokeBtn.textContent = 'REVOKE';
        revokeBtn.addEventListener('click', async () => {
          await fetch(`/auth/tokens/${t.id}`, { method: 'DELETE' });
          loadTokens();
        });
        row.appendChild(revokeBtn);
        tokenList.appendChild(row);
      }
    } catch { /* leave list empty */ }
  }

  // ---------- actions panel ----------
  const actionsModal = document.getElementById('actionsModal');
  const actionsModalClose = document.getElementById('actionsModalClose');
  const openTokensBtn = document.getElementById('openTokensBtn');
  const actionsLogoutBtn = document.getElementById('actionsLogoutBtn');
  const deleteHistoryConfirmInput = document.getElementById('deleteHistoryConfirmInput');
  const deleteHistoryBtn = document.getElementById('deleteHistoryBtn');
  const deleteHistoryStatus = document.getElementById('deleteHistoryStatus');
  const deleteAccountConfirmInput = document.getElementById('deleteAccountConfirmInput');
  const deleteAccountBtn = document.getElementById('deleteAccountBtn');
  const profileName = document.getElementById('profileName');
  const profileEmail = document.getElementById('profileEmail');
  const saveProfileBtn = document.getElementById('saveProfileBtn');
  const profileStatus = document.getElementById('profileStatus');
  const profileCurrentPassword = document.getElementById('profileCurrentPassword');
  const profileNewPassword = document.getElementById('profileNewPassword');
  const changePasswordBtn = document.getElementById('changePasswordBtn');
  const passwordStatus = document.getElementById('passwordStatus');
  const prefLength = document.getElementById('prefLength');
  const prefFormat = document.getElementById('prefFormat');
  const prefProvider = document.getElementById('prefProvider');
  const prefDigestEmail = document.getElementById('prefDigestEmail');
  const savePrefsBtn = document.getElementById('savePrefsBtn');
  const prefsStatus = document.getElementById('prefsStatus');

  async function loadPreferences() {
    prefsStatus.textContent = '';
    try {
      const res = await fetch('/auth/preferences');
      if (!res.ok) return;
      const prefs = await res.json();
      prefLength.value = prefs.summary_length;
      prefFormat.value = prefs.summary_format;
      prefProvider.value = prefs.ai_provider;
      prefDigestEmail.checked = prefs.digest_email_enabled;
    } catch { /* leave selects at their defaults */ }
  }

  savePrefsBtn.addEventListener('click', async () => {
    prefsStatus.textContent = '';
    try {
      const res = await fetch('/auth/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          summary_length: prefLength.value,
          summary_format: prefFormat.value,
          ai_provider: prefProvider.value,
          digest_email_enabled: prefDigestEmail.checked,
        }),
      });
      prefsStatus.textContent = res.ok ? 'Saved.' : 'Something went wrong — try again.';
    } catch {
      prefsStatus.textContent = 'Something went wrong — try again.';
    }
  });

  saveProfileBtn.addEventListener('click', async () => {
    profileStatus.textContent = '';
    try {
      const res = await fetch('/auth/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ full_name: profileName.value.trim(), email: profileEmail.value.trim() }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data && data.detail ? data.detail : '');
      currentUser = data;
      renderAuthArea();
      profileStatus.textContent = 'Saved.';
    } catch (e) {
      profileStatus.textContent = e.message || 'Something went wrong — try again.';
    }
  });

  changePasswordBtn.addEventListener('click', async () => {
    passwordStatus.textContent = '';
    try {
      const res = await fetch('/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          current_password: profileCurrentPassword.value,
          new_password: profileNewPassword.value,
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data && data.detail ? data.detail : '');
      profileCurrentPassword.value = '';
      profileNewPassword.value = '';
      passwordStatus.textContent = 'Password updated.';
    } catch (e) {
      passwordStatus.textContent = e.message || 'Something went wrong — try again.';
    }
  });

  function openActionsModal() {
    deleteHistoryConfirmInput.value = '';
    deleteAccountConfirmInput.value = '';
    deleteHistoryBtn.disabled = true;
    deleteAccountBtn.disabled = true;
    deleteHistoryStatus.textContent = '';
    profileStatus.textContent = '';
    passwordStatus.textContent = '';
    profileCurrentPassword.value = '';
    profileNewPassword.value = '';
    if (currentUser) {
      profileName.value = currentUser.full_name || '';
      profileEmail.value = currentUser.email || '';
    }
    renderObsidianCurrent();
    actionsModal.classList.remove('hidden');
    loadPreferences();
  }

  function closeActionsModal() {
    actionsModal.classList.add('hidden');
  }

  actionsModalClose.addEventListener('click', closeActionsModal);
  actionsModal.addEventListener('click', (e) => {
    if (e.target === actionsModal) closeActionsModal();
  });

  openTokensBtn.addEventListener('click', () => {
    closeActionsModal();
    openTokenModal();
  });

  document.getElementById('openObsidianBtn').addEventListener('click', () => {
    closeActionsModal();
    // No item — this is pure settings, so saving just stores and confirms
    // rather than immediately firing a note across.
    openObsidianSetup(null);
  });

  actionsLogoutBtn.addEventListener('click', () => {
    closeActionsModal();
    logout();
  });

  deleteHistoryConfirmInput.addEventListener('input', () => {
    deleteHistoryBtn.disabled = deleteHistoryConfirmInput.value !== 'DELETE';
  });

  deleteAccountConfirmInput.addEventListener('input', () => {
    deleteAccountBtn.disabled = deleteAccountConfirmInput.value !== 'DELETE';
  });

  deleteHistoryBtn.addEventListener('click', async () => {
    deleteHistoryBtn.disabled = true;
    try {
      const res = await fetch('/history', { method: 'DELETE' });
      if (!res.ok) throw new Error();
      const data = await res.json();
      deleteHistoryStatus.textContent = `Deleted ${data.deleted} saved summar${data.deleted === 1 ? 'y' : 'ies'}.`;
      deleteHistoryConfirmInput.value = '';
      if (!historyView.classList.contains('hidden')) loadHistory();
    } catch {
      deleteHistoryStatus.textContent = 'Something went wrong — try again.';
      deleteHistoryBtn.disabled = false;
    }
  });

  deleteAccountBtn.addEventListener('click', async () => {
    deleteAccountBtn.disabled = true;
    try {
      const res = await fetch('/auth/account', { method: 'DELETE' });
      if (!res.ok) throw new Error();
      currentUser = null;
      renderAuthArea();
      closeActionsModal();
      showMainView();
    } catch {
      deleteAccountBtn.disabled = false;
    }
  });

  // ---------- refresh ----------
  // Re-pulls data for whichever view is currently open, so there's no
  // need to reload the browser (which on the installed/home-screen PWA
  // is genuinely awkward — there's no address bar to pull down from).
  const refreshBtn = document.getElementById('refreshBtn');

  refreshBtn.addEventListener('click', async () => {
    if (refreshBtn.classList.contains('spinning')) return; // already refreshing
    refreshBtn.classList.add('spinning');
    try {
      // Auth state first: it decides which views are even available, and
      // catches a session that expired while the tab sat open.
      await refreshAuthState();
      await loadQuestions();
      if (!currentUser) return;
      if (!queueView.classList.contains('hidden')) await loadQueue();
      else if (!glossaryView.classList.contains('hidden')) await loadGlossary();
      else if (!historyView.classList.contains('hidden')) {
        await loadCategories();
        await loadHistory();
      }
      // The summarize view has nothing server-side to re-pull; refreshing
      // auth above is the whole job there.
    } catch {
      /* leave whatever is on screen alone rather than blanking it */
    } finally {
      // Always show at least one full spin, otherwise a fast refresh just
      // flickers and reads as "nothing happened".
      setTimeout(() => refreshBtn.classList.remove('spinning'), 600);
    }
  });

  // ---------- history view ----------
  // Remembers which top-level tab was open so a page refresh lands back
  // on it instead of always resetting to one hardcoded view.
  const LAST_VIEW_KEY = 'tldw_last_view';

  function rememberView(name) {
    try { sessionStorage.setItem(LAST_VIEW_KEY, name); } catch { /* private browsing, etc — non-fatal */ }
  }

  function showMainView() {
    historyView.classList.add('hidden');
    glossaryView.classList.add('hidden');
    queueView.classList.add('hidden');
    mainView.classList.remove('hidden');
    stopQueuePolling();
    rememberView('main');
  }

  async function showHistoryView() {
    mainView.classList.add('hidden');
    glossaryView.classList.add('hidden');
    queueView.classList.add('hidden');
    historyView.classList.remove('hidden');
    stopQueuePolling();
    rememberView('history');
    await loadCategories();
    await loadHistory();
  }

  // ---------- glossary view ----------
  async function showGlossaryView() {
    mainView.classList.add('hidden');
    historyView.classList.add('hidden');
    queueView.classList.add('hidden');
    glossaryView.classList.remove('hidden');
    stopQueuePolling();
    rememberView('glossary');
    await loadGlossary();
  }

  // ---------- queue view ----------
  const queueView = document.getElementById('queueView');
  const queueList = document.getElementById('queueList');
  const queueEmpty = document.getElementById('queueEmpty');
  let queuePollTimer = null;

  function stopQueuePolling() {
    if (queuePollTimer) {
      clearInterval(queuePollTimer);
      queuePollTimer = null;
    }
  }

  function queueItemHtml(item) {
    const watchLink = `https://www.youtube.com/watch?v=${encodeURIComponent(item.video_id)}`;
    const when = new Date(item.created_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    const titleLink = `<a href="${watchLink}" target="_blank" rel="noopener" onclick="return confirm('Open this video on YouTube?')">${escapeHtml(item.video_id)}</a>`;
    if (item.status === 'failed') {
      return `<div class="queue-card queue-failed">
        <div class="queue-card-top">
          ${titleLink}
          <button class="queue-dismiss-btn mono" data-id="${item.id}" title="Dismiss">✕</button>
        </div>
        <div class="queue-status-line mono">✗ Failed — ${escapeHtml(item.error || 'something went wrong')}</div>
        <div class="queue-card-bottom">
          <span class="queue-time mono">${when}</span>
          <button class="queue-retry-btn mono" data-id="${item.id}">RETRY</button>
        </div>
      </div>`;
    }
    // Processing items can sit for a while on a slow video, but the user
    // should always be able to give up on one (e.g. queued the wrong
    // link, or it's been stuck) instead of having no way to clear it.
    const { pct, label, stalled } = queueProgress(item);
    return `<div class="queue-card">
      <div class="queue-card-top">
        ${titleLink}
        <button class="queue-dismiss-btn mono" data-id="${item.id}" title="Cancel">✕</button>
      </div>
      <div class="queue-status-line mono">${stalled ? '⚠' : '⏳'} ${escapeHtml(label)}</div>
      <div class="queue-bar"><span class="queue-bar-fill${stalled ? ' stalled' : ''}" style="width:${pct}%"></span></div>
      <div class="queue-card-bottom">
        <span class="queue-time mono">${when}</span>
        <span class="queue-pct mono">${pct}%</span>
      </div>
    </div>`;
  }

  // There's no progress signal from the server — a queued job is one
  // opaque background task — so this is an elapsed-time estimate against
  // how long these actually take, matching the phases the backend really
  // runs through. Deliberately never reaches 100%: only the job leaving
  // the queue means done, and a fake 100% that then sits there is worse
  // than an honest 90%.
  const QUEUE_PHASES = [
    { until: 8,   pct: 15, label: 'Fetching the transcript…' },
    { until: 16,  pct: 35, label: 'Pulling video details…' },
    { until: 26,  pct: 55, label: 'Reading the comments…' },
    { until: 55,  pct: 80, label: 'Writing your summary…' },
    { until: 90,  pct: 92, label: 'Wrapping up…' },
  ];
  // Past this, something has almost certainly gone wrong — the backend
  // marks genuine failures, but a hung request can leave a row parked.
  const QUEUE_STALLED_SECONDS = 240;

  function queueProgress(item) {
    const started = new Date(item.created_at).getTime();
    const elapsed = Math.max(0, (Date.now() - started) / 1000);

    if (elapsed > QUEUE_STALLED_SECONDS) {
      return { pct: 95, label: 'Taking longer than usual — you can cancel or wait', stalled: true };
    }
    for (const phase of QUEUE_PHASES) {
      if (elapsed < phase.until) return { pct: phase.pct, label: phase.label, stalled: false };
    }
    return { pct: 95, label: 'Almost there…', stalled: false };
  }

  async function loadQueue() {
    try {
      const res = await fetch('/summarize/pending');
      if (!res.ok) return;
      const items = await res.json();
      queueList.innerHTML = items.map(queueItemHtml).join('');
      queueEmpty.classList.toggle('visible', items.length === 0);
    } catch { /* leave as-is */ }
  }

  queueList.addEventListener('click', async (e) => {
    const retryBtn = e.target.closest('.queue-retry-btn');
    if (retryBtn) {
      retryBtn.disabled = true;
      retryBtn.textContent = 'RETRYING…';
      try {
        await fetch(`/summarize/pending/${retryBtn.dataset.id}/retry`, { method: 'POST' });
      } finally {
        loadQueue();
      }
      return;
    }
    const btn = e.target.closest('.queue-dismiss-btn');
    if (!btn) return;
    await fetch(`/summarize/pending/${btn.dataset.id}`, { method: 'DELETE' });
    loadQueue();
  });

  async function showQueueView() {
    mainView.classList.add('hidden');
    historyView.classList.add('hidden');
    glossaryView.classList.add('hidden');
    queueView.classList.remove('hidden');
    rememberView('queue');
    await loadQueue();
    stopQueuePolling();
    queuePollTimer = setInterval(loadQueue, 5000);
  }

  function glossaryCardHtml(term) {
    const sources = term.sources.map(s =>
      `<a href="${s.url}" target="_blank" rel="noopener">${escapeHtml(s.title || s.video_id)}</a>`
    ).join('');
    return `<div class="glossary-card">
      <div class="glossary-term mono">${escapeHtml(term.term)}</div>
      <div class="glossary-definition">${escapeHtml(term.definition)}</div>
      ${term.example ? `<div class="glossary-example">"${escapeHtml(term.example)}"</div>` : ''}
      <div class="glossary-sources">Used in: ${sources}</div>
    </div>`;
  }

  function renderGlossaryList() {
    const q = glossarySearch.value.trim().toLowerCase();
    const filtered = q
      ? glossaryData.filter(t => t.term.toLowerCase().includes(q) || t.definition.toLowerCase().includes(q))
      : glossaryData;

    glossaryList.innerHTML = filtered.map(glossaryCardHtml).join('');
    glossaryEmpty.classList.toggle('visible', filtered.length === 0);
  }

  async function loadGlossary() {
    try {
      const res = await fetch('/glossary');
      glossaryData = res.ok ? await res.json() : [];
    } catch {
      glossaryData = [];
    }
    renderGlossaryList();
  }

  backToMainFromGlossaryBtn.addEventListener('click', showMainView);
  glossarySearch.addEventListener('input', renderGlossaryList);

  addTermToggleBtn.addEventListener('click', () => {
    addTermBox.classList.remove('hidden');
    addTermToggleBtn.classList.add('hidden');
    addTermInput.value = '';
    addTermStatus.textContent = '';
  });

  addTermCancelBtn.addEventListener('click', () => {
    addTermBox.classList.add('hidden');
    addTermToggleBtn.classList.remove('hidden');
  });

  addTermConfirmBtn.addEventListener('click', async () => {
    const terms = addTermInput.value.split(',').map(t => t.trim()).filter(Boolean);
    if (terms.length === 0) return;
    addTermConfirmBtn.disabled = true;
    addTermStatus.textContent = 'Defining…';
    try {
      // One API call defines the whole batch, however many terms it is.
      const res = await fetch('/glossary/custom', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ terms }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data && data.detail ? data.detail : '');
      const existing = new Set(glossaryData.map(t => t.term.toLowerCase()));
      const fresh = data.filter(t => !existing.has(t.term.toLowerCase()));
      glossaryData = [...glossaryData, ...fresh].sort((a, b) => a.term.localeCompare(b.term));
      renderGlossaryList();
      addTermBox.classList.add('hidden');
      addTermToggleBtn.classList.remove('hidden');
    } catch (e) {
      addTermStatus.textContent = e.message || "Couldn't define those terms. Try again.";
    } finally {
      addTermConfirmBtn.disabled = false;
    }
  });

  historyTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      currentStatusTab = tab.dataset.status;
      historyTabs.forEach(t => t.classList.toggle('active', t === tab));
      loadHistory();
    });
  });

  clearFiltersBtn.addEventListener('click', () => {
    searchFilter.value = '';
    dateFilter.value = '';
    categoryFilter.value = '';
    sortFilter.value = 'newest';
    categoryPanel.classList.add('hidden');
    syncSearchClear();
    loadHistory();
  });

  // The ✕ only shows when there's something to clear, so it isn't a
  // permanent smudge on an empty field.
  const searchClearBtn = document.getElementById('searchClearBtn');

  function syncSearchClear() {
    searchClearBtn.classList.toggle('hidden', !searchFilter.value);
  }

  searchFilter.addEventListener('input', syncSearchClear);

  searchClearBtn.addEventListener('click', () => {
    const hadQuery = !!searchFilter.value.trim();
    searchFilter.value = '';
    syncSearchClear();
    searchFilter.focus();
    // Only re-query if a search was actually applied — clearing whitespace
    // the user never submitted shouldn't cost a round trip.
    if (hadQuery) loadHistory();
  });
  dateFilter.addEventListener('change', loadHistory);
  categoryFilter.addEventListener('change', loadHistory);
  sortFilter.addEventListener('change', loadHistory);
  searchFilter.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') loadHistory();
  });
  searchFilter.addEventListener('blur', loadHistory);

  async function loadCategories() {
    try {
      const res = await fetch('/history/categories');
      if (!res.ok) return;
      const categories = await res.json();
      const current = categoryFilter.value;
      categoryFilter.innerHTML = '<option value="">All categories</option>' +
        categories.map(c => `<option value="${c}">${c}</option>`).join('');
      categoryFilter.value = current;
    } catch { /* leave as-is */ }
  }

  // Backend orders CATEGORY most-specific-first (see app/summarizer.py's
  // CATEGORY prompt), so the first label in the comma-joined string is
  // this video's primary category — same logic as the mobile app's
  // src/lib/categories.ts.
  function primaryCategory(category) {
    const first = (category || '').split(',')[0].trim();
    return first || 'Other';
  }

  async function openCategoryPanel() {
    categoryPanel.classList.remove('hidden');
    categoryPanelList.innerHTML = '<div class="drawer-item">Loading…</div>';
    try {
      // Unfiltered fetch of the current status tab (ignoring search/date/
      // category filters already applied) so the panel's counts reflect
      // every category available to pick, not just the current view.
      const res = await fetch(`/history?status=${currentStatusTab}&sort=newest`);
      const items = res.ok ? await res.json() : [];
      const counts = new Map();
      for (const item of items) {
        const cat = primaryCategory(item.category);
        counts.set(cat, (counts.get(cat) || 0) + 1);
      }
      const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
      const current = categoryFilter.value;
      const rows = [`<button class="drawer-item${!current ? ' active' : ''}" data-cat="">
        <span>All</span><span class="drawer-count">${items.length}</span>
      </button>`];
      for (const [cat, count] of sorted) {
        rows.push(`<button class="drawer-item${current === cat ? ' active' : ''}" data-cat="${escapeHtml(cat)}">
          <span>${escapeHtml(cat)}</span><span class="drawer-count">${count}</span>
        </button>`);
      }
      categoryPanelList.innerHTML = rows.join('');
      categoryPanelList.querySelectorAll('.drawer-item').forEach(btn => {
        btn.addEventListener('click', () => {
          categoryFilter.value = btn.dataset.cat;
          categoryPanel.classList.add('hidden');
          loadHistory();
        });
      });
    } catch {
      categoryPanelList.innerHTML = '<div class="drawer-item">Couldn\'t load categories.</div>';
    }
  }

  categoryPanelBtn.addEventListener('click', openCategoryPanel);
  categoryPanel.addEventListener('click', (e) => {
    if (e.target === categoryPanel) categoryPanel.classList.add('hidden');
  });

  async function setHistoryStatus(id, status) {
    await fetch(`/history/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    loadHistory();
  }

  function closeAllCardMenus() {
    document.querySelectorAll('.card-menu').forEach(m => m.classList.add('hidden'));
  }
  document.addEventListener('click', closeAllCardMenus);

  function wireCardMenu(card, item) {
    const menuBtn = card.querySelector('.card-menu-btn');
    const menu = card.querySelector('.card-menu');

    function addMenuItem(label, onClick, danger) {
      const btn = document.createElement('button');
      btn.className = 'card-menu-item' + (danger ? ' danger' : '');
      btn.textContent = label;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        closeAllCardMenus();
        onClick();
      });
      menu.appendChild(btn);
      return btn;
    }

    // Export options come first — available regardless of which tab the
    // card is in, since wanting a note out of an archived summary is just
    // as likely as an unread one.
    addMenuItem('SEND TO OBSIDIAN', () => sendToObsidian(item));
    addMenuItem('COPY MARKDOWN', () => copyMarkdown(item));

    if (currentStatusTab === 'archived') {
      addMenuItem('RESTORE', () => setHistoryStatus(item.id, 'unread'));

      let confirming = false;
      const deleteBtn = addMenuItem('DELETE FOREVER', async () => {
        if (!confirming) {
          confirming = true;
          deleteBtn.textContent = 'CONFIRM?';
          menu.classList.remove('hidden');
          setTimeout(() => {
            if (confirming) { confirming = false; deleteBtn.textContent = 'DELETE FOREVER'; }
          }, 3000);
          return;
        }
        await fetch(`/history/${item.id}`, { method: 'DELETE' });
        loadHistory();
      }, true);
    } else {
      const isRead = currentStatusTab === 'read';
      addMenuItem(isRead ? 'MARK UNREAD' : 'MARK READ', () => setHistoryStatus(item.id, isRead ? 'unread' : 'read'));
      addMenuItem('ARCHIVE', () => setHistoryStatus(item.id, 'archived'));

      let confirming = false;
      const deleteBtn = addMenuItem('DELETE', async () => {
        if (!confirming) {
          confirming = true;
          deleteBtn.textContent = 'CONFIRM?';
          menu.classList.remove('hidden');
          setTimeout(() => {
            if (confirming) { confirming = false; deleteBtn.textContent = 'DELETE'; }
          }, 3000);
          return;
        }
        await fetch(`/history/${item.id}`, { method: 'DELETE' });
        loadHistory();
      }, true);
    }

    menuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const wasHidden = menu.classList.contains('hidden');
      closeAllCardMenus();
      if (wasHidden) menu.classList.remove('hidden');
    });
  }

  async function loadHistory() {
    const params = new URLSearchParams();
    params.set('status', currentStatusTab);
    if (dateFilter.value) params.set('date', dateFilter.value);
    if (categoryFilter.value) params.set('category', categoryFilter.value);
    if (searchFilter.value.trim()) params.set('q', searchFilter.value.trim());
    params.set('sort', sortFilter.value);

    historyList.innerHTML = '';
    historyEmpty.classList.remove('visible');

    try {
      const res = await fetch('/history?' + params.toString());
      if (!res.ok) return;
      const items = await res.json();

      if (items.length === 0) {
        historyEmpty.textContent = currentStatusTab === 'unread'
          ? 'No summaries yet — go summarize something.'
          : `No ${currentStatusTab} summaries.`;
        historyEmpty.classList.add('visible');
        return;
      }

      let lastDateHeading = null;
      for (const item of items) {
        const created = new Date(item.created_at);
        const dateHeading = created.toLocaleDateString(undefined, {
          weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
        });

        if (dateHeading !== lastDateHeading) {
          const heading = document.createElement('div');
          heading.className = 'history-date-heading mono';
          heading.textContent = dateHeading;
          historyList.appendChild(heading);
          lastDateHeading = dateHeading;
        }

        const timeOfDay = created.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

        const card = document.createElement('div');
        card.className = 'history-card';
        card.dataset.summaryId = item.id;   // lets restoreScrollState() re-open it
        card.innerHTML = `
          <div class="history-card-header">
            <div class="history-card-title-row">
              ${item.title ? `<div class="history-card-title">${titleLinkHtml(item)}</div>` : '<div></div>'}
              <div class="card-menu-wrap">
                <button class="card-menu-btn" type="button" aria-label="More actions">⋮</button>
                <div class="card-menu hidden"></div>
              </div>
            </div>
            ${videoDetailsHtml(item)}${channelDetailsHtml(item)}
            <div class="pill-wrap card-pill-row">${categoryPillsHtml(item.category)}</div>
            ${tagsHtml(item)}
            ${item.title_answer ? `<div class="title-answer">${escapeHtml(item.title_answer)}</div>` : ''}
            <div class="history-card-meta">
              ${item.playlist_title ? `<span class="playlist-badge mono">📼 ${escapeHtml(item.playlist_title)}</span>` : ''}
              <span class="history-card-time mono">${timeOfDay}</span>
              <button class="history-card-toggle mono">EXPAND ▾</button>
            </div>
          </div>
          <div class="history-card-summary">
            <div class="summary-view-container">
              ${item.key_points && item.key_points.length > 0 ? `
                <div class="view-toggle">
                  <div class="view-toggle-btn active mono" role="button" tabindex="0" data-view="short">SHORT</div>
                  <div class="view-toggle-btn mono" role="button" tabindex="0" data-view="questions">QUESTIONS</div>
                </div>
                <div class="summary-questions hidden">${questionsHtml(item)}</div>
                <div class="key-points-list summary-short-content">${keyPointsHtml(item.key_points, item.glossary)}</div>
              ` : `<div class="summary-long-content">${longSummaryHtml(item.summary, item.glossary)}</div>`}
            </div>
            ${chaptersHtml(item)}${commentsBlockHtml(item)}
          </div>
        `;

        const toggleBtn = card.querySelector('.history-card-toggle');
        toggleBtn.addEventListener('click', () => {
          const expanded = card.classList.toggle('expanded');
          toggleBtn.textContent = expanded ? 'COLLAPSE ▴' : 'EXPAND ▾';
        });

        wireCardMenu(card, item);

        historyList.appendChild(card);
      }
      // Cards exist now, so anything remembered from before an app switch
      // can be put back.
      restoreScrollState();
    } catch { /* leave list empty */ }
  }

  function updateSubmitLabel() {
    if (submitBtn.disabled) return; // mid-request — leave the WORKING label alone
    btnLabel.textContent = urlInput.value.trim() ? 'SUMMARIZE' : 'PASTE & SUMMARIZE';
  }

  function setLoading(isLoading) {
    submitBtn.disabled = isLoading;
    urlInput.disabled = isLoading;
    timelineWrap.classList.toggle('active', isLoading);
    btnLabel.textContent = isLoading ? 'WORKING' : '';
    if (!isLoading) updateSubmitLabel();
  }

  // There's no live progress signal from the backend (summarize is one
  // HTTP round-trip, not a stream) — this is a staged estimate tuned to
  // how long each phase typically takes, matching the backend's actual
  // call order, not literal server progress.
  const PROGRESS_STAGES = [
    { message: 'fetching the transcript', value: 15, duration: 2000 },
    { message: 'pulling video details', value: 30, duration: 2000 },
    { message: 'scanning the comments', value: 45, duration: 2500 },
    { message: 'writing your summary', value: 75, duration: 6000 },
    { message: 'wrapping up', value: 90, duration: 4000 },
  ];
  // Real requests (long videos, slower providers) can run well past the
  // fixed stages above — instead of parking at 90% and looking stuck,
  // keep creeping upward slowly so there's always visible motion until
  // the real response lands.
  const TRICKLE_MESSAGE = 'wrapping up';
  const TRICKLE_CAP = 97;
  const TRICKLE_STEP_MS = 2500;

  function runProgressStages() {
    let cancelled = false;
    let timer = null;
    let value = 0;

    function apply(stage) {
      value = stage.value;
      timelineFill.style.width = value + '%';
      timelinePlayhead.style.left = value + '%';
      statusLine.textContent = `${stage.message}… ${value}%`;
    }

    function trickle() {
      if (cancelled) return;
      if (value < TRICKLE_CAP) {
        value += 1;
        timelineFill.style.width = value + '%';
        timelinePlayhead.style.left = value + '%';
        statusLine.textContent = `${TRICKLE_MESSAGE}… ${value}%`;
      }
      timer = setTimeout(trickle, TRICKLE_STEP_MS);
    }

    let i = 0;
    timelineFill.style.width = '0%';
    timelinePlayhead.style.left = '0%';
    apply(PROGRESS_STAGES[0]);
    function advance() {
      if (cancelled) return;
      i += 1;
      if (i < PROGRESS_STAGES.length) {
        apply(PROGRESS_STAGES[i]);
        timer = setTimeout(advance, PROGRESS_STAGES[i].duration);
      } else {
        timer = setTimeout(trickle, TRICKLE_STEP_MS);
      }
    }
    timer = setTimeout(advance, PROGRESS_STAGES[0].duration);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      timelineFill.style.width = '100%';
      timelinePlayhead.style.left = '100%';
    };
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.add('visible');
    result.classList.remove('visible');
  }

  function hideError() {
    errorBox.classList.remove('visible');
  }

  // ---------- batch/playlist mode ----------
  const batchModal = document.getElementById('batchModal');
  const batchModalClose = document.getElementById('batchModalClose');
  const batchSubtitle = document.getElementById('batchSubtitle');
  const batchConfirmScreen = document.getElementById('batchConfirmScreen');
  const batchVideoList = document.getElementById('batchVideoList');
  const batchCostTable = document.getElementById('batchCostTable');
  const batchProviderSelect = document.getElementById('batchProviderSelect');
  const batchCancelBtn = document.getElementById('batchCancelBtn');
  const batchRunBtn = document.getElementById('batchRunBtn');
  const batchProgressList = document.getElementById('batchProgressList');

  const PROVIDER_LABELS = { anthropic: 'Claude', openai: 'GPT', gemini: 'Gemini' };
  let batchPreviewData = null;
  let batchRunToken = 0;

  function closeBatchModal() {
    batchRunToken++; // invalidate any in-flight run loop so it stops after its current request
    batchModal.classList.add('hidden');
  }

  batchModalClose.addEventListener('click', closeBatchModal);
  batchCancelBtn.addEventListener('click', closeBatchModal);
  batchModal.addEventListener('click', (e) => { if (e.target === batchModal) closeBatchModal(); });

  function renderBatchCostTable() {
    const checked = batchVideoList.querySelectorAll('input[type="checkbox"]:checked').length;
    const total = batchPreviewData.videos.length;
    const ratio = total > 0 ? checked / total : 0;
    batchCostTable.innerHTML = Object.entries(batchPreviewData.estimated_cost).map(([provider, cost]) => {
      const selected = provider === batchProviderSelect.value ? 'selected' : '';
      return `<div class="batch-cost-item ${selected}">
        <div class="batch-cost-label mono">${PROVIDER_LABELS[provider] || provider}</div>
        <div class="batch-cost-value mono">~$${(cost * ratio).toFixed(2)}</div>
      </div>`;
    }).join('');
  }

  async function openBatchPreview(url) {
    hideError();
    if (!currentUser) {
      showError('Sign in to summarize a playlist.');
      return;
    }
    try {
      const res = await fetch(`/playlist/preview?url=${encodeURIComponent(url)}`);
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Couldn't load that playlist.");
      }
      batchPreviewData = data;
      batchSubtitle.textContent = `${data.playlist_title} · ${data.videos.length} video${data.videos.length !== 1 ? 's' : ''}`;
      batchVideoList.innerHTML = data.videos.map((v, i) => `
        <label class="batch-video-row">
          <input type="checkbox" ${v.already_summarized ? '' : 'checked'} data-index="${i}">
          <img src="${v.thumbnail_url || ''}" alt="">
          <span>${escapeHtml(v.title || v.video_id)}${v.already_summarized ? ' <span class="already-badge mono">already summarized</span>' : ''}</span>
        </label>
      `).join('');

      let prefProvider = 'anthropic';
      try {
        const prefsRes = await fetch('/auth/preferences');
        if (prefsRes.ok) prefProvider = (await prefsRes.json()).ai_provider || 'anthropic';
      } catch { /* keep default */ }
      batchProviderSelect.value = prefProvider;

      batchVideoList.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', renderBatchCostTable);
      });
      batchProviderSelect.addEventListener('change', renderBatchCostTable);
      renderBatchCostTable();

      batchConfirmScreen.classList.remove('hidden');
      batchProgressList.classList.add('hidden');
      batchModal.classList.remove('hidden');
    } catch (err) {
      showError(err.message || "Couldn't load that playlist.");
    }
  }

  batchRunBtn.addEventListener('click', async () => {
    const selected = [...batchVideoList.querySelectorAll('input[type="checkbox"]:checked')]
      .map(cb => batchPreviewData.videos[Number(cb.dataset.index)]);
    if (selected.length === 0) return;

    const provider = batchProviderSelect.value;
    const playlistId = batchPreviewData.playlist_id;
    const playlistTitle = batchPreviewData.playlist_title;
    const myRunToken = ++batchRunToken; // this run is invalidated if the modal is closed mid-run

    batchConfirmScreen.classList.add('hidden');
    batchProgressList.classList.remove('hidden');
    batchProgressList.innerHTML = `<div class="batch-quota-banner hidden" id="batchQuotaBanner"></div>` +
      selected.map((v, i) => `
      <div class="batch-progress-row" id="batchRow${i}">
        <div class="batch-progress-status mono">queued</div>
        <span>${escapeHtml(v.title || v.video_id)}</span>
      </div>
    `).join('');
    const quotaBanner = document.getElementById('batchQuotaBanner');

    for (let i = 0; i < selected.length; i++) {
      if (myRunToken !== batchRunToken) return; // modal was closed / a new run started — stop here

      const row = document.getElementById(`batchRow${i}`);
      const statusEl = row.querySelector('.batch-progress-status');
      statusEl.textContent = 'summarizing…';
      try {
        const res = await fetch('/summarize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: `https://www.youtube.com/watch?v=${selected[i].video_id}`,
            playlist_id: playlistId,
            playlist_title: playlistTitle,
            provider,
          }),
        });
        if (myRunToken !== batchRunToken) return;
        const data = await res.json();

        if (res.status === 429) {
          // Out of quota — abort the rest of the batch rather than burning through it against the same wall.
          row.classList.add('failed');
          statusEl.textContent = '✗ out of quota';
          quotaBanner.textContent = `⚠ ${data.detail || 'Ran out of quota — stopped early.'}`;
          quotaBanner.classList.remove('hidden');
          for (let j = i + 1; j < selected.length; j++) {
            const skippedRow = document.getElementById(`batchRow${j}`);
            skippedRow.classList.add('skipped');
            skippedRow.querySelector('.batch-progress-status').textContent = 'skipped';
          }
          break;
        }

        if (!res.ok) throw new Error(data.detail || 'Failed');
        row.classList.add('done');
        statusEl.textContent = data.already_summarized ? '✓ already had it' : '✓ done';
      } catch (err) {
        if (myRunToken !== batchRunToken) return;
        row.classList.add('failed');
        statusEl.textContent = `✗ ${err.message || 'failed'}`;
      }
    }

    loadHistory();
  });

  urlInput.addEventListener('input', updateSubmitLabel);

  // On platforms where navigator.clipboard.readText() is blocked (iOS
  // Safari denies it outright — a platform policy, not something a
  // script can override), the click handler below falls back to
  // focusing the field, which surfaces iOS's own native "Paste"
  // suggestion above the keyboard. This auto-submits the instant that
  // system paste lands, so the fallback is "tap button, tap Paste" (2
  // taps) instead of needing a third tap back on Summarize.
  urlInput.addEventListener('paste', () => {
    setTimeout(() => {
      const val = urlInput.value.trim();
      if (val && (val.includes('youtube.com') || val.includes('youtu.be'))) {
        form.requestSubmit();
      }
    }, 50);
  });
  updateSubmitLabel();

  // Reads the clipboard when the field is empty so one click of the
  // (now context-aware) submit button covers both "I already have the
  // link typed" and "I just copied a link" without a separate button.
  async function resolveSubmitUrl() {
    let raw = urlInput.value.trim();
    if (raw) return raw;

    if (!navigator.clipboard || !navigator.clipboard.readText) {
      showError("This browser won't let a webpage read your clipboard automatically — paste the link manually.");
      urlInput.focus();
      return null;
    }

    try {
      raw = (await navigator.clipboard.readText()).trim();
    } catch (err) {
      // Most common real-world cause: the browser denied clipboard-read
      // permission (often because it wasn't granted the first time, or
      // this tab lost focus between the tap and the read). Surface the
      // actual reason and focus the field so a manual paste is one tap.
      showError(`Couldn't read your clipboard (${err.name || 'permission denied'}) — tap the field and paste.`);
      urlInput.focus();
      return null;
    }
    if (!raw) {
      showError('Your clipboard is empty — copy a YouTube link first.');
      return null;
    }
    if (!raw.includes('youtube.com') && !raw.includes('youtu.be')) {
      showError("That doesn't look like a YouTube link.");
      return null;
    }
    urlInput.value = raw;
    updateSubmitLabel();
    return raw;
  }

  // Safari (and some other mobile browsers) only honor the Clipboard API
  // as a "real" user gesture for a handful of trusted event types — a
  // <button type="submit">'s 'click' is one of them, but the 'submit'
  // event it triggers a tick later is not always treated the same way.
  // Doing the clipboard read directly inside 'click', before the form
  // ever submits, is the most gesture-authentic path and the most
  // broadly compatible one.
  submitBtn.addEventListener('click', async (e) => {
    if (urlInput.value.trim()) return; // has text already — let the normal submit run
    e.preventDefault();
    hideError();

    if (!navigator.clipboard || !navigator.clipboard.readText) {
      showError("This browser won't let a webpage read your clipboard automatically — tap the field and paste.");
      urlInput.focus();
      return;
    }

    let raw;
    try {
      raw = (await navigator.clipboard.readText()).trim();
    } catch (err) {
      showError(`Couldn't read your clipboard (${err.name || 'permission denied'}) — tap the field and paste.`);
      urlInput.focus();
      return;
    }
    if (!raw) {
      showError('Your clipboard is empty — copy a YouTube link first.');
      return;
    }
    if (!raw.includes('youtube.com') && !raw.includes('youtu.be')) {
      showError("That doesn't look like a YouTube link.");
      return;
    }
    urlInput.value = raw;
    updateSubmitLabel();
    form.requestSubmit();
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideError();
    const rawUrl = await resolveSubmitUrl();
    if (!rawUrl) return;
    if (rawUrl.includes('list=')) {
      openBatchPreview(rawUrl);
      return;
    }
    result.classList.remove('visible');
    setLoading(true);
    const stopProgress = runProgressStages();

    try {
      const res = await fetch('/summarize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: rawUrl }),
      });

      const data = await res.json();

      if (!res.ok) {
        const detail = Array.isArray(data.detail)
          ? data.detail.map(d => d.msg).join(' ')
          : (data.detail || 'Something went wrong. Try again.');
        throw new Error(detail);
      }

      videoId.textContent = data.video_id;
      alreadySummarizedBanner.textContent = data.already_summarized
        ? '✓ You already summarized this video — showing your saved summary.' : '';
      resultTitle.innerHTML = titleLinkHtml(data);
      resultVideoDetails.innerHTML = videoDetailsHtml(data);
      resultChannelDetails.innerHTML = channelDetailsHtml(data);
      categoryPills.innerHTML = categoryPillsHtml(data.category);
      titleAnswer.textContent = data.title_answer || '';
      lastSummaryText = data.summary || '';
      if (data.key_points && data.key_points.length > 0) {
        keyPointsList.innerHTML = keyPointsHtml(data.key_points, data.glossary);
        viewToggle.classList.remove('hidden');
      } else {
        keyPointsList.innerHTML = '';
        viewToggle.classList.add('hidden');
      }
      setSummaryView('short');
      chaptersContainer.innerHTML = chaptersHtml(data);
      commentsBlockContainer.innerHTML = commentsBlockHtml(data);
      resultTags.innerHTML = tagsHtml(data);
      // Feedback needs a saved row to attach to; anonymous summaries
      // aren't persisted, so there's nothing to answer against.
      questionsPanel.innerHTML = data.saved ? questionsHtml({ id: data.id, feedback: null }) : '';

      lastSummarizedUrl = rawUrl;
      saveInsightsBox.classList.toggle('hidden', !!currentUser);
      saveName.value = '';
      saveEmail.value = '';
      savePassword.value = '';
      saveInsightsStatus.textContent = '';

      result.classList.add('visible');
    } catch (err) {
      showError(err.message || 'Couldn\u2019t reach the server. Try again in a moment.');
    } finally {
      stopProgress();
      setLoading(false);
    }
  });

  saveInsightsBtn.addEventListener('click', async () => {
    const full_name = saveName.value.trim();
    const email = saveEmail.value.trim();
    const password = savePassword.value;
    if (!full_name || !email || password.length < 8) {
      saveInsightsStatus.textContent = 'Fill in your name, email, and an 8+ character password.';
      return;
    }
    saveInsightsBtn.disabled = true;
    saveInsightsStatus.textContent = 'Creating your account…';
    try {
      const res = await fetch('/auth/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, full_name }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data && data.detail ? data.detail : 'Something went wrong.');
      currentUser = data;
      renderAuthArea();
      // Re-run now that we're authenticated, so this exact video actually
      // lands in history (the anonymous call above was never saved).
      if (lastSummarizedUrl) {
        await fetch('/summarize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: lastSummarizedUrl }),
        });
      }
      saveInsightsBox.classList.add('hidden');
      saveInsightsStatus.textContent = '';
    } catch (e) {
      saveInsightsStatus.textContent = e.message || 'Something went wrong — try again.';
    } finally {
      saveInsightsBtn.disabled = false;
    }
  });

  copyBtn.addEventListener('click', async () => {
    // The prose summary is no longer rendered (SHORT bullets replaced it),
    // so copy from the last response rather than from the DOM.
    if (!lastSummaryText) return;
    await navigator.clipboard.writeText(lastSummaryText);
    copyBtn.textContent = 'COPIED';
    setTimeout(() => (copyBtn.textContent = 'COPY'), 1500);
  });

  (async () => {
    await refreshAuthState();
    // Before any view renders: questionsHtml() reads QUESTIONS at render
    // time and nothing re-renders the cards afterwards, so loading this
    // late leaves every Questions panel stuck on "Loading…".
    await loadQuestions();
    // A refresh should leave the user wherever they were, not bounce
    // them back to a fixed screen — restore whichever tab was last open
    // this session. First-ever load of the session (nothing remembered
    // yet) still defaults signed-in users to Library, since that's the
    // page they want most of the time once they have a history to check.
    if (currentUser) {
      let lastView = null;
      try { lastView = sessionStorage.getItem(LAST_VIEW_KEY); } catch { /* private browsing, etc */ }
      if (lastView === 'main') showMainView();
      else if (lastView === 'glossary') showGlossaryView();
      else if (lastView === 'queue') showQueueView();
      else showHistoryView();
    }

    // Landed back here from Obsidian's x-success callback, which means the
    // note was actually written — worth confirming, since the hand-off is
    // otherwise silent. Strip the marker so a later refresh doesn't
    // re-announce a save that already happened.
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.get('from') === 'obsidian') {
        showToast('Saved to Obsidian');
        params.delete('from');
        const qs = params.toString();
        history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : ''));
      }
    } catch { /* URL parsing is best-effort; never block boot on it */ }
  })();
