/** Keep animation selection and URL handling consistent across canvas viewers. */
export function getAnimationSignature(animation) {
    if (!animation) return '';
    if (animation.type === 'triframe' && Array.isArray(animation.frames)) return animation.frames.join('|');
    if (animation.type === 'layered' && Array.isArray(animation.layers)) return animation.layers.map(layer => `${layer.url}:${layer.effect}`).join('|');
    if (animation.type === 'video') return animation.video_url || animation.url || animation.id || '';
    return '';
}

export function playCanvasAnimation(renderer, animation, resolveUrl = url => url) {
    if (animation.type === 'layered') {
        renderer.playLayeredAnimation(animation.layers.map(layer => ({ ...layer, url: resolveUrl(layer.url) })));
    } else if (animation.type === 'video') {
        renderer.playVideoAnimation(resolveUrl(animation.video_url || animation.url), animation);
    } else if (animation.type === 'triframe') {
        renderer.playSequence(animation.frames.map(resolveUrl), {
            frameDuration: animation.frame_duration_ms || 1400,
            crossfadeDuration: animation.crossfade_duration_ms || 500,
        });
    }
}
