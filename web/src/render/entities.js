// The entity layer: each entity is the current frame of its sprite sheet, in world units.
import { drawLayered } from '../choreography.js';
import { EntityStore } from '../entities.js';
import { getLocale } from '../i18n/i18n.js';

const POP_MS = 350;
const FONT = 'Inter, "Segoe UI", Arial, sans-serif';

function frameOf(entity, manifest, now) {
  const anim = manifest.anims[entity.anim] || manifest.anims.idle;
  const elapsed = Math.max(0, now - (entity.animStart || 0)) / 1000;
  const index = Math.floor(elapsed * anim.fps);
  // Death plays once and holds its last frame; everything else loops.
  return { row: anim.row, column: entity.anim === 'death' ? Math.min(anim.frames - 1, index) : index % anim.frames };
}

export function drawEntities(ctx, { camera, entities, dpr, now, driving = null }) {
  const k = camera.z * dpr;
  let animating = false;
  for (const entity of entities.sorted()) {
    const { manifest } = entity.sprite;
    const image = entities.image(entity);
    if (!image.complete || !image.naturalWidth || (!manifest.anims && manifest.format !== 'layered')) { animating = true; continue; }
    const layered = manifest.format === 'layered';
    const size = EntityStore.sizeOf(entity);
    let height = entity.h || 40;
    if (layered) {
      ctx.setTransform(k, 0, 0, k, (entity.rx - camera.x) * k, (entity.ry - camera.y) * k);
      drawLayered(ctx, image, manifest, entity.plan, entity.playhead || 0);
      animating ||= !!entity.view?.playback?.running;
    } else {
      const scale = entity.scale || 1;
      const width = manifest.frameW / scale; height = manifest.frameH / scale;
      const [pivotX, pivotY] = manifest.pivot;
      // Freshly spawned entities jump out with a little overshoot.
      const age = entity.bornAt ? (now - entity.bornAt) / POP_MS : 1;
      const pop = age >= 1 ? 1 : 1 + 0.35 * Math.sin(Math.min(1, age) * Math.PI) - (1 - age) * 0.4;
      if (age < 1) animating = true;
      const { row, column } = frameOf(entity, manifest, now);
      if ((manifest.anims[entity.anim] || manifest.anims.idle).frames > 1) animating = true;
      // Sprites face the way they were drawn; flip when moving the other way.
      const flip = entity.dir && (entity.dir > 0) !== (manifest.facing === 'right') ? -1 : 1;
      ctx.setTransform(k, 0, 0, k, (entity.rx - camera.x) * k, (entity.ry - camera.y) * k);
      ctx.scale(pop * size * flip, pop * size);
      ctx.drawImage(image, column * manifest.frameW, row * manifest.frameH, manifest.frameW, manifest.frameH,
        -pivotX / scale, -pivotY / scale, width, height);

    }

    const bottom = (entity.h || height * 0.7) * size / 2;
    const screenX = (entity.rx - camera.x) * k, screenY = (entity.ry - camera.y) * k;
    const view = entity.view || {};
    // A lit fuse: the seconds left, for everyone to see.
    if (view.fuse != null) {
      ctx.setTransform(dpr, 0, 0, dpr, screenX, screenY - bottom * k - 10 * dpr);
      ctx.font = `800 20px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
      ctx.lineWidth = 4; ctx.strokeStyle = '#fff'; ctx.strokeText(String(view.fuse), 0, 0);
      ctx.fillStyle = view.fuse <= 5 ? '#d7261e' : '#252722'; ctx.fillText(String(view.fuse), 0, 0);
      animating = true;
    }
    // Rain under a raining cloud.
    if (view.raining) {
      const reach = Math.max(200, Math.max(entity.w || 0, entity.h || 0) * 1.25);
      ctx.setTransform(k, 0, 0, k, (entity.rx - camera.x) * k, (entity.ry - camera.y) * k);
      ctx.strokeStyle = 'rgba(30, 136, 229, 0.55)'; ctx.lineWidth = 1.5 / camera.z; ctx.beginPath();
      for (let i = 0; i < 46; i++) {
        const fall = ((now / 700 + i * 0.137) % 1), x = ((i * 0.618) % 1 - 0.5) * 2 * reach;
        const y = bottom * 0.5 + fall * reach * 1.6;
        ctx.moveTo(x, y); ctx.lineTo(x - 4, y + 14);
      }
      ctx.stroke();
      animating = true;
    }
    if (entity.anim === 'death') continue;
    // Health, once it is not full.
    if (entity.hp != null && entity.hp < 100 && entity.hp >= 0 && !['food', 'water', 'sky', 'remote', 'fire', 'explosive'].includes(entity.type)) {
      ctx.setTransform(dpr, 0, 0, dpr, screenX, screenY - bottom * k - 8 * dpr);
      ctx.fillStyle = 'rgba(37,39,34,0.25)'; ctx.fillRect(-16, 0, 32, 4);
      ctx.fillStyle = entity.hp > 40 ? '#329856' : '#d7261e'; ctx.fillRect(-16, 0, 32 * entity.hp / 100, 4);
    }
    // The name tag, in the viewer's language, at a constant screen size.
    const label = entity.label?.[getLocale()] || entity.label?.en;
    if (label) {
      ctx.setTransform(dpr, 0, 0, dpr, screenX, screenY + bottom * k + 4 * dpr);
      ctx.font = `600 12px ${FONT}`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      const textWidth = ctx.measureText(label).width;
      ctx.fillStyle = driving === entity.id ? 'rgba(255,220,85,0.95)' : 'rgba(255,255,255,0.85)';
      ctx.beginPath(); ctx.roundRect(-textWidth / 2 - 6, -2, textWidth + 12, 18, 9); ctx.fill();
      ctx.fillStyle = '#252722';
      ctx.fillText(label, 0, 1);
    }
  }
  return animating;
}
