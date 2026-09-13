import * as THREE from './vendor/three.module.js';

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

// A proportional reconstruction from the three reference photos, not a measured model.
export function createAquarium(canvas) {
  const renderer = new THREE.WebGLRenderer({canvas, alpha:true, antialias:true, powerPreference:'low-power'});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.6));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.18;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, 1, .1, 100);
  scene.add(new THREE.HemisphereLight(0xe4f3ee, 0x78654c, 2.15));
  const daylight = new THREE.DirectionalLight(0xfff3d8, 3.0);
  daylight.position.set(-5, 10, 8);
  daylight.castShadow = true;
  daylight.shadow.mapSize.set(1024, 1024);
  Object.assign(daylight.shadow.camera, {left:-8, right:8, top:6, bottom:-6, near:1, far:25});
  daylight.shadow.bias = -.001;
  daylight.shadow.normalBias = .045;
  scene.add(daylight);
  const fillLight = new THREE.DirectionalLight(0xdcece9, 1.1);
  fillLight.position.set(6, 5, -2);
  scene.add(fillLight);
  const habitat = new THREE.Group();
  scene.add(habitat);
  const textures = [];
  let seed = 15489;
  const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  const between = (a, b) => a + (b-a) * random();
  const material = (color, extra={}) => new THREE.MeshStandardMaterial({color, roughness:.88, ...extra});
  function texture(size, paint, repeatX=1, repeatY=1) {
    const element = document.createElement('canvas'); element.width = element.height = size;
    paint(element.getContext('2d'), size);
    const map = new THREE.CanvasTexture(element); map.colorSpace = THREE.SRGBColorSpace;
    map.wrapS = map.wrapT = THREE.RepeatWrapping; map.repeat.set(repeatX, repeatY);
    map.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy()); textures.push(map); return map;
  }
  const soilMap = texture(256, (ctx,s) => {
    ctx.fillStyle='#554636';ctx.fillRect(0,0,s,s);
    for(let i=0;i<7200;i++) {
      const v=Math.floor(between(35,108));ctx.fillStyle=`rgba(${v+12},${v},${Math.floor(v*.73)},${between(.25,.8)})`;
      ctx.fillRect(random()*s,random()*s,between(.5,2.8),between(.5,2.3));
    }
    for(let i=0;i<70;i++) {
      const x=random()*s,y=random()*s;ctx.strokeStyle=i%3?'#6a5a40':'#302b24';ctx.lineWidth=.6;
      ctx.beginPath();ctx.moveTo(x,y);ctx.bezierCurveTo(x+7,y-3,x+12,y+8,x+between(14,30),y+between(-9,9));ctx.stroke();
    }
  }, 4, 2);
  const stoneMap = texture(256, (ctx,s) => {
    ctx.fillStyle='#b7b3a8';ctx.fillRect(0,0,s,s);
    for(let i=0;i<9000;i++) { const v=Math.floor(between(50,180));ctx.fillStyle=`rgba(${v+5},${v+4},${v},${between(.12,.52)})`;ctx.fillRect(random()*s,random()*s,between(1,4),between(1,3)); }
  }, 2, 2);
  const mossMap = texture(128, (ctx,s) => {
    ctx.fillStyle='#454d22';ctx.fillRect(0,0,s,s);
    for(let i=0;i<2200;i++) {ctx.fillStyle=['#617333','#788138','#364524','#97933b'][i%4];ctx.globalAlpha=between(.25,.75);ctx.fillRect(random()*s,random()*s,between(1,4),between(1,3));}ctx.globalAlpha=1;
  });
  const weaveMap = texture(256, (ctx,s) => {
    ctx.fillStyle='#332921';ctx.fillRect(0,0,s,s);
    for(let row=0;row<8;row++)for(let col=-1;col<5;col++) {
      const x=col*64+(row%2)*32,y=row*32;
      ctx.lineCap='round';ctx.lineWidth=12;ctx.strokeStyle='#211b18';ctx.beginPath();ctx.moveTo(x,y+17);ctx.bezierCurveTo(x+15,y+5,x+48,y+5,x+64,y+17);ctx.stroke();
      ctx.lineWidth=10;ctx.strokeStyle='#76675a';ctx.beginPath();ctx.moveTo(x,y+14);ctx.bezierCurveTo(x+15,y+4,x+48,y+4,x+64,y+14);ctx.stroke();
      ctx.lineWidth=2;ctx.strokeStyle='#a18e78';ctx.beginPath();ctx.moveTo(x+2,y+10);ctx.bezierCurveTo(x+19,y+3,x+44,y+3,x+61,y+10);ctx.stroke();
    }
  }, 2, 1.25);
  const frameMat = material(0x493d34, {roughness:.66, metalness:.1});
  const frameEdgeMat = material(0x78695a, {roughness:.65, metalness:.16});
  const darkMat = material(0x302923);
  const soilMat = material(0xaca18d, {map:soilMap});
  const rockMat = material(0xffffff, {map:stoneMap, bumpMap:stoneMap, bumpScale:.025, roughness:1});
  const pebbleMat = material(0xffffff, {roughness:.67});
  const greenMat = material(0x07995c, {roughness:.48});
  const orangeMat = material(0xe66f31, {roughness:.72});
  const connectorMat = material(0x282e2a, {roughness:.7});
  const glassMat = new THREE.MeshPhysicalMaterial({color:0xc9e0d8, transparent:true, opacity:.075, roughness:.12, metalness:.02, side:THREE.DoubleSide, depthWrite:false});
  const edgeMat = new THREE.LineBasicMaterial({color:0xc9e5dc, transparent:true, opacity:.46});
  function box(w,h,d,x,y,z,mat=frameMat,parent=habitat) {
    const mesh=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),mat);mesh.position.set(x,y,z);mesh.castShadow=mesh.receiveShadow=!mat.transparent;parent.add(mesh);return mesh;
  }
  function sphere(parent,mat,x,y,z,sx,sy,sz) {
    const mesh=new THREE.Mesh(new THREE.SphereGeometry(1,16,10),mat);mesh.position.set(x,y,z);mesh.scale.set(sx,sy,sz);mesh.castShadow=true;mesh.receiveShadow=true;parent.add(mesh);return mesh;
  }
  function tube(points,radius,mat=greenMat,segments=40,parent=habitat) {
    const curve=new THREE.CatmullRomCurve3(points.map(p=>new THREE.Vector3(...p)));
    const mesh=new THREE.Mesh(new THREE.TubeGeometry(curve,segments,radius,8,false),mat);mesh.castShadow=true;parent.add(mesh);return {mesh,curve};
  }
  function rod(start,end,radius,mat=frameMat,parent=habitat) {
    const a=new THREE.Vector3(...start),b=new THREE.Vector3(...end),delta=b.clone().sub(a);
    const mesh=new THREE.Mesh(new THREE.CylinderGeometry(radius,radius,delta.length(),10),mat);
    mesh.position.copy(a).add(b).multiplyScalar(.5);mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),delta.normalize());mesh.castShadow=true;parent.add(mesh);return mesh;
  }
  function glass(w,h,d,x,y,z) {
    const pane=box(w,h,d,x,y,z,glassMat);
    const lines=new THREE.LineSegments(new THREE.EdgesGeometry(pane.geometry),edgeMat);lines.position.copy(pane.position);habitat.add(lines);
  }
  const dummy = new THREE.Object3D();
  function instances(geometry,mat,items) {
    const mesh=new THREE.InstancedMesh(geometry,mat,items.length);
    items.forEach((item,i)=>{
      dummy.position.set(...item.p);dummy.scale.set(...(item.s||[1,1,1]));dummy.rotation.set(...(item.r||[0,0,0]));dummy.updateMatrix();mesh.setMatrixAt(i,dummy.matrix);
      if(item.c!==undefined)mesh.setColorAt(i,new THREE.Color(item.c));
    });
    mesh.castShadow=mesh.receiveShadow=true;habitat.add(mesh);return mesh;
  }

  // The low pool is on the left; the raised soil enclosure occupies the right two thirds.
  box(12.3,.22,4.55,0,.01,0,frameMat);
  box(12.0,.15,4.27,0,.19,0,material(0x6b6654,{map:stoneMap}));
  box(8.1,.79,4.12,1.9,.64,0,soilMat);
  box(12.18,.11,.12,0,.26,2.18,frameEdgeMat);
  box(12.18,.11,.12,0,.26,-2.18,frameEdgeMat);
  const wallItems=[];
  box(8.2,2.84,.15,1.95,2.33,-2.16,darkMat);
  box(.15,2.84,4.29,6.02,2.33,0,darkMat);
  // Recessed square plastic wall panels, as in the enclosure photographs.
  for(let row=0;row<7;row++)for(let col=0;col<19;col++) {
    wallItems.push({p:[-1.98+col*.428,.99+row*.415,-2.047],s:[.353,.323,.085],c:(row+col)%3?0x514337:0x5d4d3e});
  }
  for(let row=0;row<7;row++)for(let col=0;col<9;col++) {
    wallItems.push({p:[5.93,.99+row*.415,-1.8+col*.44],s:[.085,.323,.36],c:(row+col)%3?0x514337:0x5d4d3e});
  }
  instances(new THREE.BoxGeometry(1,1,1),frameMat,wallItems);
  for(const x of [-2.2,1.85,6.03]) {
    box(.2,3.58,.22,x,2.01,-2.12);
    box(.032,3.32,.025,x-.06,2.0,-1.99,frameEdgeMat);
  }
  box(8.3,.16,.24,1.94,3.83,-2.12);
  box(.22,.16,4.34,6.02,3.83,0);
  const wovenMat=material(0xddd5c8,{map:weaveMap,roughness:.82});
  for(const [x,w] of [[-.15,3.72],[3.91,3.72]]) {
    box(w,1.04,.12,x,.82,2.13,wovenMat);
    box(w,.12,.2,x,1.35,2.13);
    glass(w,1.48,.025,x,2.15,2.13);
  }
  glass(.025,1.51,4.17,-6.0,1.03,0);
  glass(3.73,1.51,.025,-4.1,1.03,2.13);
  glass(3.73,2.43,.025,-4.1,1.49,-2.13);
  for(const [x,z,h] of [[-6,-2.14,2.78],[-6,2.14,1.91],[-2.12,2.14,3.01],[1.87,2.14,3.05],[6.02,2.14,3.92]]) {
    box(.2,h,.2,x,.24+h/2,z);
    box(.032,h-.16,.032,x-.059,.24+h/2,z+.112,frameEdgeMat);
    box(.28,.11,.28,x,.29+h,z,darkMat);
  }
  // A slimmer glass partition at the wet/dry transition is interrupted by the ramp.
  glass(.025,.74,1.05,-2.12,1.65,1.6);
  glass(.025,1.2,1.1,-2.12,1.88,-1.57);

  const channelPoints=[[-2.28,1.055,-.08],[-1.25,1.12,-.28],[.1,1.17,-.55],[1.25,1.2,-.45],[2.3,1.22,-.65],[3.07,1.23,-.55]];
  const channelCurve=new THREE.CatmullRomCurve3(channelPoints.map(p=>new THREE.Vector3(...p)));
  function channelZ(x) {
    const t=clamp((x+2.28)/5.35,0,1);return channelCurve.getPoint(t).z;
  }
  const terrain=new THREE.PlaneGeometry(8.05,4.08,72,34);terrain.rotateX(-Math.PI/2);
  const pos=terrain.attributes.position;
  for(let i=0;i<pos.count;i++) {
    const x=pos.getX(i)+1.92,z=pos.getZ(i), basin=Math.hypot((x-3.58)/1.0,(z+.58)/.88);
    const channelDistance=Math.abs(z-channelZ(x));
    let y=1.08+Math.sin(x*2.3+z*3.1)*.032+random()*.045;
    if(x<3.2&&channelDistance<.44)y=.97+channelDistance*.12;
    if(basin<1.1)y=.98+Math.min(.1,Math.max(0,basin-.82)*.4);
    pos.setXYZ(i,x,y,z);
  }
  terrain.computeVertexNormals();const soilSurface=new THREE.Mesh(terrain,soilMat);soilSurface.receiveShadow=true;habitat.add(soilSurface);
  const stoneGeometry=new THREE.IcosahedronGeometry(1,2);
  const stonePositions=stoneGeometry.attributes.position;
  for(let i=0;i<stonePositions.count;i++) {
    const x=stonePositions.getX(i),y=stonePositions.getY(i),z=stonePositions.getZ(i);
    // Coordinate-based distortion keeps shared triangle vertices together.
    const r=1+.09*Math.sin(x*8.7+y*11.3+z*5.8);stonePositions.setXYZ(i,x*r,y*r,z*r);
  }
  stoneGeometry.computeVertexNormals();
  const bank=[];
  for(let i=0;i<25;i++) {
    const t=i/24,p=channelCurve.getPoint(t),tangent=channelCurve.getTangent(t);
    for(const side of [-1,1]) {
      const width=between(.38,.55);
      bank.push({p:[p.x-tangent.z*side*.51,1.17+between(-.035,.055),p.z+tangent.x*side*.51],s:[between(.23,.39),between(.13,.23),width*.55],r:[random()*.24,random()*Math.PI,.14*random()],c:i%4===0?0x9f9a8d:0xbdb9ac});
    }
  }
  for(let i=0;i<17;i++) {
    const a=i/17*Math.PI*2;
    if(a>2.57&&a<3.74)continue;
    bank.push({p:[3.58+Math.cos(a)*1.13,1.2,-.58+Math.sin(a)*.91],s:[between(.25,.4),between(.15,.26),between(.24,.37)],r:[random()*.3,a,random()*.2],c:i%3===0?0xaaa599:0xc1bcaf});
  }
  instances(stoneGeometry,rockMat,bank);
  const pebbles=[];
  const pebbleColors=[0xc3b999,0xeee1bc,0x8e8578,0x645e4e,0xb49c79,0x777d75,0x9b7460];
  for(let i=0;i<128;i++) {
    const p=channelCurve.getPoint(random()),s=between(.055,.115);
    pebbles.push({p:[p.x,1.045+random()*.025,p.z+between(-.32,.32)],s:[s*between(1,1.4),s*.54,s],r:[random(),random()*3,random()],c:pebbleColors[i%pebbleColors.length]});
  }
  for(let i=0;i<95;i++) {
    const x=between(-5.8,-2.34),z=between(-1.9,1.9),s=between(.075,.18);
    pebbles.push({p:[x,.34+random()*.02,z],s:[s,s*.5,s*.8],r:[random(),random()*3,random()],c:pebbleColors[i%pebbleColors.length]});
  }
  for(let i=0;i<90;i++) {
    const x=between(-1.9,5.75),z=between(-1.93,1.93);
    if((x<3.2&&Math.abs(z-channelZ(x))<.75)||Math.hypot(x-3.58,z+.58)<1.4)continue;
    const s=between(.07,.16);pebbles.push({p:[x,1.14,z],s:[s,s*.72,s*.85],r:[random(),random()*3,random()],c:i%3?0x665644:0x8d7860});
  }
  instances(stoneGeometry,pebbleMat,pebbles);
  const mossMat=material(0xc5c39e,{map:mossMap,roughness:.94});
  const basinRock=new THREE.Mesh(stoneGeometry,mossMat);basinRock.position.set(3.6,1.19,-.62);basinRock.scale.set(.68,.2,.57);basinRock.rotation.y=.35;basinRock.castShadow=basinRock.receiveShadow=true;habitat.add(basinRock);
  const mossPatches=[];
  for(let i=0;i<13;i++) {const a=i/13*Math.PI*2;mossPatches.push({p:[3.58+Math.cos(a)*.95,1.16,-.58+Math.sin(a)*.77],s:[.16,.055,.12],r:[0,a,0]});}
  instances(stoneGeometry,mossMat,mossPatches);

  // Orange half-pipe and the narrow ribbed access ramp seen beside the water.
  const rampShape=new THREE.Shape();
  rampShape.moveTo(-.5,0);rampShape.quadraticCurveTo(0,-.31,.5,0);rampShape.lineTo(.5,.065);rampShape.quadraticCurveTo(0,-.245,-.5,.065);rampShape.closePath();
  const ramp=new THREE.Mesh(new THREE.ExtrudeGeometry(rampShape,{depth:1.28,bevelEnabled:false,curveSegments:18}),orangeMat);
  const orangeRampGroup=new THREE.Group();orangeRampGroup.position.set(-2.91,.69,1.33);orangeRampGroup.rotation.z=.46;habitat.add(orangeRampGroup);
  ramp.rotation.y=Math.PI/2;ramp.castShadow=ramp.receiveShadow=true;orangeRampGroup.add(ramp);
  const rampGroup=new THREE.Group();rampGroup.position.set(-2.34,.78,.67);rampGroup.rotation.z=.56;habitat.add(rampGroup);
  box(1.25,.085,.48,0,0,0,material(0x746458),rampGroup);
  for(let i=0;i<11;i++)box(.034,.035,.47,-.57+i*.112,.06,0,frameEdgeMat,rampGroup);

  // The bright green perimeter irrigation tube has static, unused spray nozzles.
  const perimeter=tube([[-5.98,2.78,-1.77],[-5.57,2.42,-1.93],[-4.62,2.15,-1.91],[-3.28,2.46,-1.96],[-2.04,2.8,-1.98],[.45,2.72,-1.98],[2.85,2.79,-1.98],[5.58,2.73,-1.96],[5.78,2.65,-1.7],[5.79,2.48,.2],[5.73,2.27,1.85]],.061);
  for(const [x,y,z] of [[-4.52,2.17,-1.91],[-.2,2.73,-1.98],[3.18,2.78,-1.98],[5.78,2.49,.14]]) {
    const connector=new THREE.Group();connector.position.set(x,y,z);if(x>5)connector.rotation.y=Math.PI/2;habitat.add(connector);
    rod([-.17,0,0],[.17,0,0],.115,connectorMat,connector);
    rod([0,0,0],[0,-.22,0],.078,connectorMat,connector);
    rod([0,-.22,0],[0,-.35,0],.084,orangeMat,connector);
    for(const xx of [-.14,.14]) {const band=new THREE.Mesh(new THREE.TorusGeometry(.117,.012,5,14),darkMat);band.rotation.y=Math.PI/2;band.position.x=xx;connector.add(band);}
  }
  const tieMat=material(0xc2c5aa);
  for(const x of [-3.18,-1.63,1.01,2.29,4.38,5.37]) {
    let nearest=perimeter.curve.getPoint(0),distance=Infinity;
    for(let i=0;i<=300;i++){const point=perimeter.curve.getPoint(i/300);if(point.z< -1.8&&Math.abs(point.x-x)<distance){nearest=point;distance=Math.abs(point.x-x);}}
    const tie=new THREE.Mesh(new THREE.TorusGeometry(.078,.009,4,10),tieMat);tie.rotation.y=Math.PI/2;tie.position.copy(nearest);habitat.add(tie);
  }
  // The return hose is a visual route only; its pump is not connected to the controls.
  tube([[3.57,1.16,-.87],[3.66,1.5,-1.15],[3.82,2.1,-1.99],[3.8,3.58,-2.0],[3.5,3.93,-2.05],[3.12,3.83,-2.25]],.046,greenMat,30);
  const clearHoseMat=material(0xb6c4ac,{transparent:true,opacity:.54,roughness:.4});
  tube([[-5.2,.43,-.55],[-5.42,.51,-1.34],[-5.55,1.72,-1.84],[-5.53,2.7,-2.1]],.045,clearHoseMat,28);
  const filterMat=material(0x464f45);
  box(.72,.23,.42,-5.14,.47,-.62,filterMat);
  for(let i=0;i<8;i++)box(.026,.008,.37,-5.44+i*.085,.592,-.62,frameEdgeMat);

  const waterMat=new THREE.MeshPhysicalMaterial({color:0x588f83,transparent:true,opacity:.25,roughness:.15,metalness:.05,side:THREE.DoubleSide,depthWrite:false});
  const surfaceMat=new THREE.MeshPhysicalMaterial({color:0x9fc6b6,transparent:true,opacity:.27,roughness:.17,metalness:.04,side:THREE.DoubleSide,depthWrite:false});
  const water=box(3.77,1,4.07,-4.08,.7,0,waterMat);
  const surface=new THREE.Mesh(new THREE.PlaneGeometry(3.77,4.07),surfaceMat);surface.rotation.x=-Math.PI/2;surface.position.set(-4.08,.94,0);habitat.add(surface);
  const basin=new THREE.Mesh(new THREE.CircleGeometry(1,44),surfaceMat);basin.rotation.x=-Math.PI/2;basin.position.set(3.58,1.105,-.58);basin.scale.set(1.03,.83,1);habitat.add(basin);
  function ribbon(curve,width) {
    const vertices=[],indices=[];
    for(let i=0;i<=42;i++) {
      const t=i/42,p=curve.getPoint(t),d=curve.getTangent(t),n=new THREE.Vector3(-d.z,0,d.x).normalize().multiplyScalar(width/2);
      vertices.push(p.x+n.x,p.y,p.z+n.z,p.x-n.x,p.y,p.z-n.z);
      if(i<42){const k=i*2;indices.push(k,k+2,k+1,k+1,k+2,k+3);}
    }
    const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));geo.setIndex(indices);geo.computeVertexNormals();return geo;
  }
  const shallowCurve=new THREE.CatmullRomCurve3(channelPoints.map(p=>new THREE.Vector3(p[0],1.11,p[2])));
  const stream=new THREE.Mesh(ribbon(shallowCurve,.65),surfaceMat);habitat.add(stream);
  const motionMat=new THREE.MeshBasicMaterial({color:0xd7eee0,transparent:true,opacity:.58,depthWrite:false});
  const currents=[];
  for(let i=0;i<11;i++) {
    const dash=new THREE.Mesh(new THREE.SphereGeometry(1,6,4),motionMat);dash.scale.set(.058,.009,.018);habitat.add(dash);currents.push(dash);
  }
  const spill=new THREE.Mesh(new THREE.PlaneGeometry(.48,1),new THREE.MeshBasicMaterial({color:0x95c6af,transparent:true,opacity:.2,side:THREE.DoubleSide,depthWrite:false}));
  spill.rotation.y=Math.PI/2;spill.position.set(-2.28,.88,-.08);habitat.add(spill);
  const ripples=[];
  for(let i=0;i<3;i++) {
    const ring=new THREE.Mesh(new THREE.TorusGeometry(.17,.009,5,36),new THREE.MeshBasicMaterial({color:0xc6e8d5,transparent:true,opacity:.35,depthWrite:false}));
    ring.rotation.x=-Math.PI/2;habitat.add(ring);ripples.push(ring);
  }
  const flowParticles=[];
  for(const kind of ['fill','drain'])for(let i=0;i<8;i++) {
    const drop=new THREE.Mesh(new THREE.SphereGeometry(.035,6,5),motionMat);habitat.add(drop);flowParticles.push({drop,kind,phase:i/8});
  }

  const turtleSkin=material(0x777c43,{roughness:.84}),turtlePale=material(0xb2a265),turtleShell=material(0x4a5532,{roughness:.68});
  const turtleRed=material(0xd34b30),turtleEye=material(0x101913,{roughness:.3});
  const shellLineMat=new THREE.LineBasicMaterial({color:0xa69d62,transparent:true,opacity:.67});
  const turtles=[];
  function turtle(x,z,size,heading) {
    const group=new THREE.Group();group.position.set(x,.65,z);group.scale.setScalar(size);group.rotation.y=heading;habitat.add(group);
    sphere(group,turtlePale,0,.12,0,.51,.12,.67);
    sphere(group,turtleShell,0,.25,0,.55,.27,.7);
    // Shell plates curve with the dome instead of triangulating the whole animal.
    for(const side of [-1,1])for(let i=0;i<3;i++) {
      const z0=-.46+i*.34,points=[];
      for(let j=0;j<=10;j++){const x0=side*(.04+j*.043),zz=z0+Math.sin(j/10*Math.PI)*.045;const y0=.25+.278*Math.sqrt(Math.max(0,1-(x0/.555)**2-(zz/.71)**2));points.push(new THREE.Vector3(x0,y0,zz));}
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(points),shellLineMat));
    }
    const spine=[];for(let i=0;i<22;i++){const zz=-.64+i*.061;spine.push(new THREE.Vector3(0,.25+.28*Math.sqrt(Math.max(0,1-(zz/.7)**2)),zz));}
    group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(spine),shellLineMat));
    sphere(group,turtleSkin,0,.17,.74,.17,.15,.29);
    sphere(group,turtlePale,0,.08,.88,.12,.052,.11);
    for(const side of [-1,1]) {
      sphere(group,turtleRed,side*.152,.18,.727,.016,.043,.098);
      sphere(group,turtleEye,side*.144,.23,.863,.027,.029,.025);
      for(let i=0;i<2;i++)sphere(group,turtlePale,side*.15,.12+i*.065,.83,.012,.011,.13);
    }
    const feet=[];
    for(const [xx,zz] of [[-.45,.4],[.45,.4],[-.44,-.42],[.44,-.42]]) {
      const pivot=new THREE.Group();pivot.position.set(xx,.09,zz);group.add(pivot);
      const foot=sphere(pivot,turtleSkin,Math.sign(xx)*.095,-.025,zz>0?.11:-.09,.19,.07,.23);foot.rotation.y=Math.sign(xx)*.4;feet.push(pivot);
      for(let i=0;i<3;i++)rod([Math.sign(xx)*.18,-.02,-.085+i*.055],[Math.sign(xx)*.25,-.025,-.1+i*.055],.009,turtlePale,pivot);
    }
    const tail=new THREE.Mesh(new THREE.ConeGeometry(.065,.3,7),turtleSkin);tail.rotation.x=-Math.PI/2;tail.position.set(0,.1,-.77);group.add(tail);
    turtles.push({group,feet,x,z,heading,size});
  }
  turtle(-4.55,.65,.71,-.72);
  turtle(-3.36,-.82,.58,1.16);

  let level=.65,targetLevel=.65,frameId=0,lastTime=0,lastRender=0,view='perspective';
  let flow={fill:false,drain:false,circulation:true};
  function setLevel(percent) { if(Number.isFinite(percent))targetLevel=clamp(percent/100,0,1); }
  function setFlow(value={}) {flow={...flow,...value};}
  function setView(value) {if(['front','top','perspective'].includes(value)){view=value;resize();}}
  function resize() {
    const width=canvas.clientWidth,height=canvas.clientHeight;if(!width||!height)return;
    renderer.setSize(width,height,false);camera.aspect=width/height;
    const target=new THREE.Vector3(0,1.83,0);
    const direction=(view==='top'?new THREE.Vector3(0,1,.001):view==='front'?new THREE.Vector3(0,4.9,16):new THREE.Vector3(-5.4,height<170?9.2:12.8,15.6)).normalize();
    const upReference=view==='top'?new THREE.Vector3(0,0,-1):new THREE.Vector3(0,1,0);
    const right=new THREE.Vector3().crossVectors(upReference,direction).normalize();
    const up=new THREE.Vector3().crossVectors(direction,right);
    const vertical=Math.tan(THREE.MathUtils.degToRad(camera.fov/2)),horizontal=vertical*camera.aspect;
    let distance=0;
    for(const x of [-6.25,6.25])for(const y of [-.12,4.06])for(const z of [-2.35,2.33]) {
      const p=new THREE.Vector3(x,y,z).sub(target);
      distance=Math.max(distance,p.dot(direction)+Math.abs(p.dot(right))/horizontal,p.dot(direction)+Math.abs(p.dot(up))/vertical);
    }
    camera.up.copy(upReference);camera.position.copy(target).addScaledVector(direction,distance*1.035);camera.lookAt(target);camera.updateProjectionMatrix();
  }
  const observer=new ResizeObserver(resize);observer.observe(canvas);
  const prefersReducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
  function draw(time) {
    frameId=requestAnimationFrame(draw);
    if(document.hidden||canvas.clientWidth===0||time-lastRender<32)return;
    const dt=Math.min(.1,(time-lastTime)/1000||.033);lastTime=time;lastRender=time;
    const t=prefersReducedMotion.matches?0:time*.001;
    level+=(targetLevel-level)*(1-Math.exp(-dt*3));
    // Only the left pool follows estimated water level; circulation never changes that estimate.
    const waterHeight=.70*level,waterY=.345+waterHeight;
    water.visible=surface.visible=level>.004;water.scale.y=Math.max(.001,waterHeight);water.position.y=.345+waterHeight/2;surface.position.y=waterY;
    const circulating=flow.circulation&&level>.025;
    currents.forEach((dash,i)=>{
      dash.visible=circulating&&!prefersReducedMotion.matches;
      const p=shallowCurve.getPoint(1-((t*.14+i/11)%1)),d=shallowCurve.getTangent(1-((t*.14+i/11)%1));
      dash.position.set(p.x,1.126,p.z+Math.sin(i*2.4)*.12);dash.rotation.y=-Math.atan2(d.z,d.x);
    });
    spill.visible=circulating&&waterY<1.105;spill.scale.y=Math.max(.005,1.11-waterY);spill.position.y=(1.11+waterY)/2;
    ripples.forEach((ring,i)=>{
      ring.visible=circulating;const phase=(t*.43+i/3)%1;
      ring.position.set(-2.65,waterY+.008,-.08);ring.scale.setScalar(.6+phase*2.2);ring.material.opacity=(1-phase)*.29;
    });
    flowParticles.forEach(({drop,kind,phase})=>{
      const p=(t*.65+phase)%1;drop.visible=Boolean(flow[kind])&&!prefersReducedMotion.matches;
      if(kind==='fill')drop.position.set(-5.48,waterY+.05+(1-p)*.72,-1.63);
      else drop.position.set(-5.23+Math.sin(p*4)*.025,Math.max(.37,waterY-.05-p*.62),-.6);
    });
    turtles.forEach((item,index)=>{
      const swimming=level>.16;
      item.group.position.y=swimming?Math.max(.38,waterY-item.size*.26)+Math.sin(t*(1.12+index*.17))*.014:.38;
      item.group.position.x=item.x+(swimming?Math.sin(t*.28+index)*.13:0);
      item.group.position.z=item.z+(swimming?Math.cos(t*.3+index)*.1:0);
      item.group.rotation.y=item.heading+(swimming?Math.sin(t*.22+index)*.13:0);
      item.feet.forEach((foot,i)=>foot.rotation.y=swimming?Math.sin(t*1.8+i*1.7+index)*.18:0);
    });
    renderer.render(scene,camera);
  }
  resize();draw(33);
  return {setLevel,setFlow,setView,dispose(){
    cancelAnimationFrame(frameId);observer.disconnect();
    const geometries=new Set(),materials=new Set();scene.traverse(object=>{if(object.geometry)geometries.add(object.geometry);if(object.material)(Array.isArray(object.material)?object.material:[object.material]).forEach(mat=>materials.add(mat));});
    geometries.forEach(geometry=>geometry.dispose());materials.forEach(mat=>mat.dispose());textures.forEach(map=>map.dispose());renderer.dispose();
  }};
}
