// The author's prepared drawing, before any motion is applied.
import { getLocale, t } from '../i18n/i18n.js';
import { element } from './dom.js';

const SVG = 'http://www.w3.org/2000/svg';
const COLORS = ['#c02b87', '#156dc1', '#17804e', '#b56300'];
function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.append(...children);
  return node;
}

export function createPartsPreview(stage) {
  let data = null, selected = null;
  const summary = element('summary');
  const content = element('div', { class: 'parts-preview-content' });
  const root = element('details', { class: 'parts-preview', hidden: true, open: true }, summary, content);
  stage.append(root);

  function refresh() {
    if (!data) return;
    summary.textContent = t('alive.partsTitle');
    const [width, height] = data.size, unit = Math.max(width, height) / 256;
    const picture = svg('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': t('alive.partsImage') },
      svg('image', { href: data.image, width, height }));
    const options = [];
    for (const [i, part] of data.drawing.parts.entries()) {
      const color = COLORS[i % COLORS.length], [nx, ny, nw, nh] = part.box;
      const [x, y, w, h] = [nx * width, ny * height, nw * width, nh * height];
      const [px, py] = [part.pivot[0] * width, part.pivot[1] * height];
      picture.append(svg('g', { opacity: selected && selected !== part.id ? 0.2 : 1 },
        svg('rect', { x, y, width: w, height: h, fill: color, 'fill-opacity': 0.08, stroke: color, 'stroke-width': unit }),
        svg('circle', { cx: px, cy: py, r: 3 * unit, fill: 'white', stroke: color, 'stroke-width': 1.5 * unit }),
        svg('path', { d: `M ${px-5*unit} ${py} h ${10*unit} M ${px} ${py-5*unit} v ${10*unit}`, stroke: color, 'stroke-width': unit })));
      const option = element('button', { class: 'chip parts-preview-option', type: 'button',
        'aria-pressed': selected === part.id ? 'true' : 'false',
        onclick: () => { selected = selected === part.id ? null : part.id; refresh(); } }, part.label[getLocale()]);
      option.style.borderColor = color;
      options.push(option);
    }
    content.replaceChildren(picture,
      element('p', { class: 'parts-preview-help' }, t(data.drawing.parts.length ? 'alive.partsHint' : 'alive.partsEmpty')),
      element('div', { class: 'parts-preview-options' }, ...options));
  }

  return {
    set(next) { data = next; selected = null; root.hidden = false; root.open = true; refresh(); },
    clear() { data = null; content.replaceChildren(); root.hidden = true; },
    refresh,
    destroy() { root.remove(); },
  };
}
