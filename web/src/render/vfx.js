// Short-lived effects for world events: blasts, bites, hearts, puffs, splashes.
const KINDS = {
  explode: { life: 700 },
  eat: { life: 500, color: '#8d6e3f', count: 6, spread: 26 },
  hit: { life: 350, color: '#d7261e', count: 5, spread: 22 },
  birth: { life: 1100, glyph: '♥', color: '#ef5f8f', count: 4, spread: 34, rise: 46 },
  pet: { life: 900, glyph: '♥', color: '#ef5f8f', count: 1, spread: 6, rise: 40 },
  death: { life: 700, color: '#9a9a9a', count: 8, spread: 34, rise: 18 },
  drink: { life: 600, color: '#1e88e5', count: 5, spread: 18, rise: 14 },
  catch: { life: 700, color: '#1e88e5', count: 9, spread: 30, rise: 26 },
  douse: { life: 900, color: '#c9d3d6', count: 9, spread: 26, rise: 50 },
  ignite: { life: 600, color: '#fb8c00', count: 8, spread: 28, rise: 30 },
  cook: { life: 800, color: '#c9d3d6', count: 4, spread: 12, rise: 36 },
  fruit: { life: 400, color: '#43a047', count: 4, spread: 16 },
  shake: { life: 500, color: '#43a047', count: 7, spread: 40 },
};

export class Vfx {
  constructor() { this.effects = []; }

  // A world:event; unknown kinds are simply not shown.
  add(event, now) {
    const kind = KINDS[event.type];
    if (!kind || event.x == null) return;
    this.effects.push({ ...kind, type: event.type, x: event.x, y: event.y, r: event.r || 0, start: now, seed: this.effects.length * 2.4 + now % 7 });
  }

  // Draws in world coordinates; returns true while anything is still playing.
  draw(ctx, { camera, dpr, now }) {
    const k = camera.z * dpr;
    this.effects = this.effects.filter(effect => now - effect.start < effect.life);
    for (const effect of this.effects) {
      const t = (now - effect.start) / effect.life;
      ctx.setTransform(k, 0, 0, k, (effect.x - camera.x) * k, (effect.y - camera.y) * k);
      if (effect.type === 'explode') {
        const radius = effect.r * (0.25 + 0.75 * Math.sqrt(t));
        ctx.beginPath(); ctx.arc(0, 0, radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 196, 60, ${0.75 * (1 - t)})`; ctx.fill();
        ctx.lineWidth = 6 * (1 - t) + 1; ctx.strokeStyle = `rgba(215, 38, 30, ${1 - t})`; ctx.stroke();
        ctx.beginPath(); ctx.arc(0, 0, radius * 0.45, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 255, 255, ${0.9 * (1 - t) ** 2})`; ctx.fill();
        continue;
      }
      ctx.globalAlpha = 1 - t;
      for (let i = 0; i < effect.count; i++) {
        const angle = effect.seed + i * 2.399;
        const x = Math.cos(angle) * effect.spread * (0.3 + 0.7 * t);
        const y = Math.sin(angle) * effect.spread * (0.3 + 0.7 * t) * 0.6 - (effect.rise || 0) * t;
        ctx.fillStyle = effect.color;
        if (effect.glyph) {
          ctx.font = `${16 / camera.z}px sans-serif`; ctx.textAlign = 'center';
          ctx.fillText(effect.glyph, x, y);
        } else {
          ctx.beginPath(); ctx.arc(x, y, 3.2 * (1 - t * 0.5), 0, Math.PI * 2); ctx.fill();
        }
      }
      ctx.globalAlpha = 1;
    }
    return this.effects.length > 0;
  }
}
