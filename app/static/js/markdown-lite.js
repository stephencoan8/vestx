/**
 * Small Markdown subset renderer for VestX Advisor.
 * Supports: headings, bold/italic, lists, tables, code, paragraphs, links.
 * No HTML pass-through (escaped).
 */
(function (global) {
  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function inline(s) {
    s = esc(s);
    // code
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    // bold
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    // italic
    s = s.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
    // links [t](url)
    s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }

  function renderMarkdown(src) {
    if (!src) return '';
    var lines = String(src).replace(/\r\n/g, '\n').split('\n');
    var html = [];
    var i = 0;
    var inUl = false;
    var inOl = false;
    var inCode = false;
    var codeBuf = [];

    function closeLists() {
      if (inUl) { html.push('</ul>'); inUl = false; }
      if (inOl) { html.push('</ol>'); inOl = false; }
    }

    while (i < lines.length) {
      var line = lines[i];

      // fenced code
      if (/^```/.test(line)) {
        if (inCode) {
          html.push('<pre class="md-pre"><code>' + esc(codeBuf.join('\n')) + '</code></pre>');
          codeBuf = [];
          inCode = false;
        } else {
          closeLists();
          inCode = true;
        }
        i++;
        continue;
      }
      if (inCode) {
        codeBuf.push(line);
        i++;
        continue;
      }

      // table block
      if (line.indexOf('|') !== -1 && i + 1 < lines.length && /^\s*\|?[\s:-]+\|/.test(lines[i + 1])) {
        closeLists();
        var rows = [];
        while (i < lines.length && lines[i].indexOf('|') !== -1) {
          if (!/^\s*\|?[\s:-]+\|/.test(lines[i])) {
            rows.push(lines[i]);
          }
          i++;
          if (i < lines.length && lines[i].indexOf('|') === -1) break;
        }
        if (rows.length) {
          html.push('<div class="md-table-wrap"><table class="md-table">');
          rows.forEach(function (r, ri) {
            var cells = r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|');
            var tag = ri === 0 ? 'th' : 'td';
            html.push('<tr>' + cells.map(function (c) {
              return '<' + tag + '>' + inline(c.trim()) + '</' + tag + '>';
            }).join('') + '</tr>');
          });
          html.push('</table></div>');
        }
        continue;
      }

      // headings
      var hm = /^(#{1,3})\s+(.+)$/.exec(line);
      if (hm) {
        closeLists();
        var lvl = hm[1].length;
        html.push('<h' + (lvl + 1) + ' class="md-h">' + inline(hm[2]) + '</h' + (lvl + 1) + '>');
        i++;
        continue;
      }

      // hr
      if (/^---+$/.test(line.trim())) {
        closeLists();
        html.push('<hr class="md-hr"/>');
        i++;
        continue;
      }

      // ul
      var um = /^[-*]\s+(.+)$/.exec(line);
      if (um) {
        if (inOl) { html.push('</ol>'); inOl = false; }
        if (!inUl) { html.push('<ul class="md-ul">'); inUl = true; }
        html.push('<li>' + inline(um[1]) + '</li>');
        i++;
        continue;
      }

      // ol
      var om = /^\d+\.\s+(.+)$/.exec(line);
      if (om) {
        if (inUl) { html.push('</ul>'); inUl = false; }
        if (!inOl) { html.push('<ol class="md-ol">'); inOl = true; }
        html.push('<li>' + inline(om[1]) + '</li>');
        i++;
        continue;
      }

      // blank
      if (!line.trim()) {
        closeLists();
        i++;
        continue;
      }

      // paragraph
      closeLists();
      html.push('<p class="md-p">' + inline(line) + '</p>');
      i++;
    }
    closeLists();
    if (inCode) {
      html.push('<pre class="md-pre"><code>' + esc(codeBuf.join('\n')) + '</code></pre>');
    }
    return html.join('\n');
  }

  global.vestxMarkdown = { render: renderMarkdown, esc: esc };
})(typeof window !== 'undefined' ? window : globalThis);
