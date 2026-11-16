// The floating toolbar: tool choice, palette, brush width, zoom.
import { t } from '../i18n/i18n.js';
import { element } from './dom.js';

const TOOLS = [['draw', '✎', 'tool.draw'], ['hand', '✋', 'tool.hand'], ['alive', '✨', 'tool.alive']];

export function createTools({ palette, limits, canEdit, onTool, onZoom }) {
  const state = {
    tool: canEdit ? 'draw' : 'hand',
    color: palette[0],
    width: Math.max(limits.brushWidthMin, Math.min(6, limits.brushWidthMax)),
  };
  const toolButtons = TOOLS.filter(([name]) => canEdit || name === 'hand').map(([name, icon, key]) => element('button', {
    class: 'tool-button', type: 'button', 'data-tool': name, 'data-i18n-label': key, 'data-i18n-title': key,
    onclick: () => { state.tool = name; refresh(); onTool(name); },
  }, icon));
  const colors = element('div', { class: 'palette', role: 'group', 'data-i18n-label': 'tool.colors' });
  for (const value of palette) {
    const button = element('button', {
      class: 'color-button', type: 'button', 'data-color': value,
      onclick: () => { state.color = value; refresh(); },
    });
    button.style.setProperty('--color', value);
    colors.append(button);
  }
  const preview = element('span', {});
  const slider = element('input', {
    type: 'range', class: 'width-slider', step: '1', min: limits.brushWidthMin, max: limits.brushWidthMax,
    'data-i18n-label': 'tool.width',
    oninput: () => { state.width = Number(slider.value); refresh(); },
  });
  slider.value = state.width;
  const brush = canEdit ? [element('span', { class: 'tool-divider' }), colors, slider, element('div', { class: 'brush-preview' }, preview)] : [];
  const el = element('aside', { class: 'tools', 'data-i18n-label': 'tool.group' },
    ...toolButtons, ...brush, element('span', { class: 'tool-divider' }),
    element('button', { class: 'tool-button', type: 'button', 'data-i18n-label': 'tool.zoomIn', 'data-i18n-title': 'tool.zoomIn', onclick: () => onZoom(1.4) }, '+'),
    element('button', { class: 'tool-button', type: 'button', 'data-i18n-label': 'tool.zoomOut', 'data-i18n-title': 'tool.zoomOut', onclick: () => onZoom(1 / 1.4) }, '−'));

  function refresh() {
    for (const button of toolButtons) button.setAttribute('aria-pressed', String(button.dataset.tool === state.tool));
    for (const button of colors.children) {
      button.setAttribute('aria-pressed', String(button.dataset.color === state.color));
      button.setAttribute('aria-label', t('tool.color', { color: button.dataset.color }));
    }
    const dot = Math.max(3, Math.min(state.width, 28));
    preview.style.cssText = `width:${dot}px;height:${dot}px;background:${state.color}`;
  }
  refresh();
  return { el, state, refresh };
}
