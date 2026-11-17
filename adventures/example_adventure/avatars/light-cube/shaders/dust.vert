varying vec3 surface;
varying vec3 viewDirection;

void main() {
  surface = position;
  // In the avatar's orthographic camera, every ray travels along view-space -Z.
  viewDirection = normalize(vec3(modelViewMatrix[0].z, modelViewMatrix[1].z, modelViewMatrix[2].z));
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
