const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const smooth = value => value * value * (3 - 2 * value);
const mix = (a, b, value) => a + (b - a) * value;

// Decorative animal movement is independent of the pumps and estimated water volume.
// The shared land excursion gives the narrow access ramp one visitor at a time.
export function createTurtleAnimator(THREE, turtles) {
  const rampZ = .72;
  const landZ = 1.28;
  const rampSlope = Math.tan(.56);
  const rampHeight = x => .842 + (x + 2.34) * rampSlope + .018;
  const landHeight = 1.16;
  const swimmingPeriod = 45;
  const point = new THREE.Vector3();
  const tangent = new THREE.Vector3();
  const contactPoint = new THREE.Vector3();
  const contactRotation = new THREE.Matrix4();
  const contactEuler = new THREE.Euler(0, 0, 0, 'YXZ');
  const contactSamples = [];
  // Lower envelopes of the belly, shell and head; limb samples include the claws.
  // These use the same local proportions as the visible turtle meshes.
  for (const [cx, cy, cz, rx, ry, rz] of [
    [0, .12, 0, .51, .12, .67], [0, .25, 0, .55, .27, .7],
    [0, .17, .74, .17, .15, .29], [0, .08, .88, .12, .052, .11],
    [-.545, .065, .51, .19, .07, .23], [.545, .065, .51, .19, .07, .23],
    [-.535, .065, -.51, .19, .07, .23], [.535, .065, -.51, .19, .07, .23]
  ]) {
    for (let ring = 0; ring <= 5; ring++) {
      const latitude = -Math.PI / 2 + ring * Math.PI / 10;
      for (let column = 0; column < 16; column++) {
        const longitude = column * Math.PI / 8;
        contactSamples.push(new THREE.Vector3(
          cx + rx * Math.cos(latitude) * Math.cos(longitude),
          cy + ry * Math.sin(latitude),
          cz + rz * Math.cos(latitude) * Math.sin(longitude)
        ));
      }
    }
  }
  contactSamples.push(new THREE.Vector3(0, .035, -.92));
  const curve = (points, closed = false) => new THREE.CatmullRomCurve3(
    points.map(([x, z]) => new THREE.Vector3(x, 0, z)), closed, 'centripetal'
  );
  // The loop passes in front of the low filter and leaves space beside the ramp.
  const swimmingPath = curve([
    [-4.43, 1.10], [-5.02, .74], [-4.86, .38], [-4.22, -.02],
    [-3.77, -.74], [-3.55, -.28], [-3.79, .64]
  ], true);
  const states = turtles.map((item, index) => ({
    ...item,
    swimPhase: index === 0 ? 0 : .1,
    gait: index * Math.PI,
    heading: Math.PI / 2,
    pitch: 0,
    positioned: false,
    footRest: item.feet.map(foot => ({
      position: foot.position.clone(), rotation: foot.rotation.clone()
    }))
  }));
  let visitor = 0;
  let phase = 0;
  let phaseTime = 0;
  let elapsed = 0;
  let plan = [];
  let disposed = false;

  function excursion(start, first = false) {
    const approach = first
      ? [[-3.28, rampZ], [-3.12, rampZ], [-2.96, rampZ]]
      : [[start.x, start.z], [-3.40, .32], [-3.24, rampZ], [-2.96, rampZ]];
    const exit = [[-1.81, rampZ], [-.95, rampZ], [-.55, landZ]];
    return [
      {name: 'approach', duration: 5, path: curve(approach), surface: 'pool'},
      {name: 'climb', duration: 9, path: curve([[-2.96, rampZ], [-1.81, rampZ]]), surface: 'ramp'},
      {name: 'landing', duration: 5, path: curve(exit), surface: 'landing'},
      {name: 'outbound', duration: 10, path: curve([[-.55, landZ], [3.9, landZ]]), surface: 'land'},
      {name: 'rest', duration: 3, at: [3.9, landZ], heading: Math.PI / 2, surface: 'land'},
      {name: 'turn', duration: 2, at: [3.9, landZ], heading: Math.PI / 2, surface: 'land'},
      {name: 'homebound', duration: 10, path: curve([[3.9, landZ], [-.55, landZ]]), surface: 'land'},
      {name: 'return-landing', duration: 5, path: curve([...exit].reverse()), surface: 'landing'},
      {name: 'descend', duration: 9, path: curve([[-1.81, rampZ], [-2.96, rampZ]]), surface: 'ramp'},
      {name: 'retreat', duration: 6, path: curve([[-2.96, rampZ], [-3.48, 1.02], [-4.43, 1.10]]), surface: 'pool'}
    ];
  }

  function waterPose(item, level, waterY) {
    const swimming = level > .18 && waterY > .47;
    return {
      swimming,
      y: swimming ? Math.max(.415, waterY - item.size * .21) : .415
    };
  }

  function contactHeight(x, z) {
    if (x >= -2.15) return landHeight;
    let ground = .39;
    if (x > -3.16 && Math.abs(z - rampZ) < .66) {
      const alongRamp = smooth(clamp((x + 3.16) / .29, 0, 1));
      const acrossRamp = smooth(clamp((.66 - Math.abs(z - rampZ)) / .235, 0, 1));
      ground = Math.max(ground, mix(ground, rampHeight(x) - .018, alongRamp * acrossRamp));
    }
    // Anticipate the raised soil edge while the forefeet approach it. This avoids
    // driving the extended neck through the vertical lip before the body arrives.
    if (x > -2.43) ground = mix(ground, landHeight, smooth(clamp((x + 2.43) / .28, 0, 1)));
    return ground;
  }

  function positionAnimal(item, segment, fraction, level, waterY, dt, reducedMotion) {
    let heading;
    if (segment.path) {
      segment.path.getPointAt(fraction, point);
      segment.path.getTangentAt(fraction, tangent);
      heading = Math.atan2(tangent.x, tangent.z);
    } else {
      point.set(segment.at[0], 0, segment.at[1]);
      heading = segment.heading - (segment.name === 'turn' ? Math.PI * smooth(fraction) : 0);
    }
    const water = waterPose(item, level, waterY);
    const frontReach = item.size * .93;
    const rearReach = item.size * .77;
    const forwardX = Math.sin(heading), forwardZ = Math.cos(heading);
    const frontContact = Math.max(water.y, contactHeight(point.x + forwardX * frontReach, point.z + forwardZ * frontReach));
    const rearContact = Math.max(water.y, contactHeight(point.x - forwardX * rearReach, point.z - forwardZ * rearReach));
    const pitch = clamp(-Math.atan2(frontContact - rearContact, frontReach + rearReach), -.56, .56);
    const swimming = water.swimming && (segment.surface === 'pool'
      || (segment.surface === 'ramp' && rampHeight(point.x) < water.y));
    const resting = segment.name === 'rest';
    const climbing = segment.surface === 'ramp' && !swimming;
    const activity = resting ? 'rest' : swimming ? 'swim' : climbing ? 'climb' : 'walk';
    if (!reducedMotion) item.gait += dt * (swimming ? 4.4 : resting ? 0 : 6.4);
    const sway = resting ? 0 : Math.sin(item.gait);
    const bob = resting ? 0 : (swimming ? .012 : .008) * Math.sin(item.gait * 2);
    // Longitudinal pitch aligns the turtle's belly and feet with the inclined boards.
    const response = item.positioned ? 1 - Math.exp(-dt * 7) : 1;
    const angle = Math.atan2(Math.sin(heading - item.heading), Math.cos(heading - item.heading));
    const angularStep = item.positioned ? clamp(angle * response, -dt * 2.4, dt * 2.4) : angle;
    item.heading += angularStep;
    item.heading = Math.atan2(Math.sin(item.heading), Math.cos(item.heading));
    item.pitch += (pitch - item.pitch) * response;
    const roll = sway * (swimming ? .035 : .016);
    contactRotation.makeRotationFromEuler(contactEuler.set(item.pitch, item.heading, roll, 'YXZ'));
    let y = water.y + bob;
    // Supporting the entire lower hull prevents a tilted head or tail dipping
    // through the floor at either end of the ramp, including an empty pool.
    for (const sample of contactSamples) {
      contactPoint.copy(sample).multiplyScalar(item.size).applyMatrix4(contactRotation);
      y = Math.max(y, contactHeight(point.x + contactPoint.x, point.z + contactPoint.z) - contactPoint.y + .012);
    }
    item.group.position.set(point.x, y, point.z);
    item.group.rotation.set(item.pitch, item.heading, roll, 'YXZ');
    item.positioned = true;
    item.group.name ||= `turtle-${states.indexOf(item) + 1}`;
    Object.assign(item.group.userData, {
      activity,
      motionPhase: segment.name,
      onLand: segment.surface === 'land' || segment.surface === 'landing',
      motionTime: elapsed
    });
    if (reducedMotion) return;
    item.feet.forEach((foot, index) => {
      const rest = item.footRest[index];
      const diagonal = index === 0 || index === 3 ? 0 : Math.PI;
      const kick = resting ? 0 : Math.sin(item.gait + diagonal);
      const side = Math.sign(rest.position.x);
      foot.position.copy(rest.position);
      // Tuck the limbs toward the body on the narrower ribbed access ramp.
      if (climbing) foot.position.x *= .88;
      foot.position.y += resting ? 0 : Math.max(0, kick) * (swimming ? .014 : .025);
      foot.rotation.copy(rest.rotation);
      foot.rotation.y += kick * (swimming ? .57 : .24);
      foot.rotation.x += kick * (swimming ? .16 : .11);
      foot.rotation.z += side * (swimming ? -.14 : climbing ? -.08 : 0);
    });
  }

  if (states.length) {
    states[0].group.position.set(-3.28, .65, rampZ);
    plan = excursion(states[0].group.position, true);
  }

  return {
    update(dt, level, waterY, reducedMotion = false) {
      if (disposed || !states.length) return;
      const step = reducedMotion ? 0 : clamp(Number.isFinite(dt) ? dt : 0, 0, .1);
      elapsed += step;
      phaseTime += step;
      while (phaseTime >= plan[phase].duration) {
        phaseTime -= plan[phase].duration;
        phase += 1;
        if (phase === plan.length) {
          states[visitor].swimPhase = 0;
          visitor = (visitor + 1) % states.length;
          plan = excursion(states[visitor].group.position);
          phase = 0;
        }
      }
      states.forEach((item, index) => {
        if (index === visitor) {
          positionAnimal(item, plan[phase], phaseTime / plan[phase].duration, level, waterY, step, reducedMotion);
        } else {
          item.swimPhase = (item.swimPhase + step / swimmingPeriod) % 1;
          positionAnimal(item, {name: 'pool', path: swimmingPath, surface: 'pool'}, item.swimPhase, level, waterY, step, reducedMotion);
        }
      });
    },
    dispose() { disposed = true; }
  };
}
