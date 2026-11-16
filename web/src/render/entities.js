// The entity layer: each entity is a frame of its sprite sheet, sized in world units.
import { getLocale } from '../i18n/i18n.js';

const POP_MS = 350;

export function drawEntities(ctx, { camera, entities, dpr, now }) {
  const k = camera.z * dpr;
  let animating = false;
  for (const entity of entities.sorted()) {
    const { manifest } = entity.sprite;
    const image = entities.image(entity);
    if (!image.complete || !image.naturalWidth) { animating = true; continue; }
    const scale = entity.scale || 1;
    const width = manifest.frameW / scale, height = manifest.frameH / scale;
    const [pivotX, pivotY] = manifest.pivot;
    // Freshly spawned entities jump out with a little overshoot.
    const age = entity.bornAt ? (now - entity.bornAt) / POP_MS : 1;
    const pop = age >= 1 ? 1 : 1 + 0.35 * Math.sin(Math.min(1, age) * Math.PI) - (1 - age) * 0.4;
    if (age < 1) animating = true;
    const anim = manifest.anims.idle;
    ctx.setTransform(k, 0, 0, k, (entity.x - camera.x) * k, (entity.y - camera.y) * k);
    ctx.scale(pop, pop);
    ctx.drawImage(image, 0, anim.row * manifest.frameH, manifest.frameW, manifest.frameH,
      -pivotX / scale, -pivotY / scale, width, height);
    // The name tag, in the viewer's language, at a constant screen size.
    const label = entity.label?.[getLocale()] || entity.label?.en;
    if (label) {
      ctx.setTransform(dpr, 0, 0, dpr, (entity.x - camera.x) * k, (entity.y - camera.y + (height - pivotY / scale)) * k + 4 * dpr);
      ctx.font = '600 12px Inter, "Segoe UI", Arial, sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      const textWidth = ctx.measureText(label).width;
      ctx.fillStyle = 'rgba(255,255,255,0.85)';
      ctx.beginPath(); ctx.roundRect(-textWidth / 2 - 6, -2, textWidth + 12, 18, 9); ctx.fill();
      ctx.fillStyle = '#252722';
      ctx.fillText(label, 0, 1);
    }
  }
  return animating;
}
