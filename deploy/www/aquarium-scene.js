import * as THREE from './vendor/three.module.js';

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

export function createAquarium(canvas) {
  const renderer = new THREE.WebGLRenderer({canvas, alpha:true, antialias:true, powerPreference:'low-power'});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.6));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.5;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(39, 1, .1, 100);
  scene.add(new THREE.HemisphereLight(0xd6fff3, 0x26505f, 2.1));
  const sun = new THREE.DirectionalLight(0xffedc6, 3.4);
  sun.position.set(-4, 9, 7);
  scene.add(sun);
  const sideLight = new THREE.PointLight(0x81f7f1, 19, 17);
  sideLight.position.set(4, 5, -3);
  scene.add(sideLight);

  const tank = new THREE.Group();
  scene.add(tank);
  const glass = new THREE.MeshPhysicalMaterial({color:0xa8e4e7, transparent:true, opacity:.095, roughness:.08, metalness:.04, side:THREE.DoubleSide, depthWrite:false});
  const pane = (w,h,d,x,y,z) => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w,h,d), glass);
    mesh.position.set(x,y,z);
    tank.add(mesh);
  };
  pane(8.9,4.15,.035,0,2.22,-2.55);
  pane(8.9,4.15,.035,0,2.22,2.55);
  pane(.035,4.15,5.1,-4.45,2.22,0);
  pane(.035,4.15,5.1,4.45,2.22,0);
  const frame = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(8.92,4.2,5.12)),
    new THREE.LineBasicMaterial({color:0x91e2e4, transparent:true, opacity:.64}));
  frame.position.y = 2.23;
  tank.add(frame);
  const base = new THREE.Mesh(new THREE.BoxGeometry(9.25,.26,5.4), new THREE.MeshStandardMaterial({color:0x153d46,metalness:.24,roughness:.45}));
  base.position.y=.08;
  tank.add(base);
  const sand = new THREE.Mesh(new THREE.BoxGeometry(8.73,.14,4.92), new THREE.MeshStandardMaterial({color:0xb6a988,roughness:1}));
  sand.position.y=.29;
  tank.add(sand);

  const waterMaterial = new THREE.MeshPhysicalMaterial({color:0x3da6b9,transparent:true,opacity:.18,roughness:.18,metalness:.04,side:THREE.DoubleSide,depthWrite:false});
  const water = new THREE.Mesh(new THREE.BoxGeometry(8.77,1,4.97),waterMaterial);
  tank.add(water);
  const surface = new THREE.Mesh(new THREE.PlaneGeometry(8.73,4.93),new THREE.MeshPhysicalMaterial({color:0x71d5d3,transparent:true,opacity:.32,roughness:.12,metalness:.05,side:THREE.DoubleSide,depthWrite:false}));
  surface.rotation.x=-Math.PI/2;
  tank.add(surface);
  const rippleMaterial = new THREE.MeshBasicMaterial({color:0xb5f8ec,transparent:true,opacity:.28,depthWrite:false});
  const ripples = [0,1,2].map(i=>{
    const ring = new THREE.Mesh(new THREE.TorusGeometry(.37+i*.23,.012,5,48),rippleMaterial);
    ring.rotation.x=Math.PI/2;
    ring.position.set(-1.7+i*.24,2,1.05+i*.12);
    tank.add(ring);
    return ring;
  });

  const rockMaterial = new THREE.MeshStandardMaterial({color:0x817969,roughness:1,flatShading:true});
  const landMaterial = new THREE.MeshStandardMaterial({color:0xbaa47c,roughness:1,flatShading:true});
  const rock = (x,y,z,sx,sy,sz,material=rockMaterial) => {
    const mesh = new THREE.Mesh(new THREE.IcosahedronGeometry(1,1),material);
    mesh.position.set(x,y,z);mesh.scale.set(sx,sy,sz);tank.add(mesh);return mesh;
  };
  rock(2.72,1.05,-1.4,1.08,1.02,.94);
  rock(2.54,2.15,-1.4,.9,1.05,.83);
  rock(2.55,3.14,-1.32,1.15,.51,1.0,landMaterial);
  rock(2.76,3.34,-1.52,.78,.19,.71,landMaterial);
  for(let i=0;i<35;i++){
    const x=-3.9+(i*1.683)%7.9,z=-2.15+(i*2.217)%4.25;
    if(x>1.25&&z<-.4)continue;
    const color=i%4===0?0x9c9281:i%4===1?0xb9ae91:i%4===2?0x687c70:0xd3c39b;
    rock(x,.4,z,.09+(i%4)*.025,.05+(i%3)*.018,.07+(i%5)*.012,new THREE.MeshStandardMaterial({color,roughness:1}));
  }
  const leafMaterial = new THREE.MeshStandardMaterial({color:0x478c69,side:THREE.DoubleSide,roughness:.85});
  const stemMaterial = new THREE.MeshStandardMaterial({color:0x3a7057,roughness:.9});
  for(const [x,z,h] of [[-3.6,-1.8,1.35],[-3.1,-1.9,1.05],[.4,-2.1,1.2],[3.75,1.6,.9]]){
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(.025,.055,h,6),stemMaterial);
    stem.position.set(x,.38+h/2,z);stem.rotation.z=(x%2)*.08;tank.add(stem);
    for(let j=0;j<3;j++){
      const leaf=new THREE.Mesh(new THREE.SphereGeometry(1,9,6),leafMaterial);
      leaf.scale.set(.11,.35,.04);leaf.position.set(x+(j-1)*.19,.63+j*h*.23,z+(j%2)*.1);
      leaf.rotation.z=(j-1)*.65;tank.add(leaf);
    }
  }

  const turtles=[];
  function turtle(x,y,z,size,rotation){
    const group=new THREE.Group();group.position.set(x,y,z);group.rotation.y=rotation;group.scale.setScalar(size);tank.add(group);
    const skin=new THREE.MeshStandardMaterial({color:0x758d52,roughness:.83});
    const pale=new THREE.MeshStandardMaterial({color:0xb3ae73,roughness:.87});
    const shellMaterial=new THREE.MeshStandardMaterial({color:0x405e3d,roughness:.7,metalness:.05,flatShading:true});
    const seam=new THREE.LineBasicMaterial({color:0xb3b36e,transparent:true,opacity:.76});
    const sphere=(parent,mat,sx,sy,sz,px,py,pz)=>{const m=new THREE.Mesh(new THREE.SphereGeometry(1,18,12),mat);m.scale.set(sx,sy,sz);m.position.set(px,py,pz);parent.add(m);return m;};
    sphere(group,pale,.91,.23,1.06,0,.17,0);
    const geo=new THREE.IcosahedronGeometry(1,1);
    const shell=new THREE.Mesh(geo,shellMaterial);shell.scale.set(1.02,.49,1.15);shell.position.y=.42;group.add(shell);
    const lines=new THREE.LineSegments(new THREE.EdgesGeometry(geo),seam);lines.scale.copy(shell.scale).multiplyScalar(1.005);lines.position.copy(shell.position);group.add(lines);
    sphere(group,skin,.31,.28,.4,0,.27,1.28);
    sphere(group,pale,.19,.11,.19,0,.15,1.58);
    const red=new THREE.MeshStandardMaterial({color:0xdb4b39,roughness:.8});
    const black=new THREE.MeshStandardMaterial({color:0x12292a,roughness:.4});
    for(const side of [-1,1]){
      sphere(group,red,.065,.085,.17,side*.282,.3,1.23);
      sphere(group,black,.047,.055,.051,side*.267,.44,1.47);
    }
    const fins=[];
    for(const [fx,fz] of [[-.78,.68],[.78,.68],[-.75,-.67],[.75,-.67]]){
      const pivot=new THREE.Group();pivot.position.set(fx,.15,fz);group.add(pivot);
      const fin=sphere(pivot,skin,.28,.11,.47,fx<0?-.23:.23,-.08,fz>0?.14:-.16);
      fin.rotation.y=fx<0?-.35:.35;fins.push(pivot);
    }
    const tail=new THREE.Mesh(new THREE.ConeGeometry(.15,.45,8),skin);tail.rotation.x=-Math.PI/2;tail.position.set(0,.12,-1.21);group.add(tail);
    turtles.push({group,fins,baseY:y});
  }
  turtle(-1.75,1.25,.36,.76,.24);
  turtle(2.55,3.59,-1.2,.53,-.7);
  const bubbleMaterial=new THREE.MeshBasicMaterial({color:0xc6fcf7,transparent:true,opacity:.36,depthWrite:false});
  const bubbles=[];
  for(let i=0;i<14;i++){
    const b=new THREE.Mesh(new THREE.SphereGeometry(1,7,6),bubbleMaterial);
    b.scale.setScalar(.025+(i%4)*.014);b.position.set(-3.4+(i*1.27)%6.9,.65+(i*.31)%2,-1.9+(i*.83)%3.7);tank.add(b);bubbles.push(b);
  }
  let level=.65, target=.65, frameId=0;
  function setLevel(percent){target=clamp(percent/100,0,1);}
  function resize(){
    const width=canvas.clientWidth,height=canvas.clientHeight;
    if(!width||!height)return;
    renderer.setSize(width,height,false);
    camera.aspect=width/height;
    camera.position.set(width<570?8.6:7.7,width<570?6.3:5.9,width<570?15:10.8);
    camera.lookAt(0,2.05,0);
    camera.updateProjectionMatrix();
  }
  const observer=new ResizeObserver(resize);observer.observe(canvas);
  function draw(time){
    frameId=requestAnimationFrame(draw);
    if(document.hidden||canvas.clientWidth===0)return;
    const t=time*.001;
    level+=(target-level)*.035;
    // "Full" is the visual operating level below the basking island, not the tank rim.
    const waterHeight=2.85*level;
    water.visible=surface.visible=level>.004;
    water.scale.y=Math.max(.001,waterHeight);
    water.position.y=.4+waterHeight/2;
    surface.position.y=.4+waterHeight+.003;
    ripples.forEach((ring,i)=>{ring.visible=level>.08;ring.position.y=surface.position.y+.016;const scale=.65+((t*.2+i*.3)%1)*.8;ring.scale.setScalar(scale);});
    turtles[0].group.position.y=clamp(.58+waterHeight*.29,.58,1.65)+Math.sin(t*1.5)*.055;
    turtles[0].group.position.x=-1.75+Math.sin(t*.45)*.15;
    turtles[0].group.rotation.y=.24+Math.sin(t*.42)*.13;
    turtles[1].group.position.y=turtles[1].baseY+Math.sin(t*.7)*.015;
    turtles.forEach((item,index)=>item.fins.forEach((fin,j)=>{fin.rotation.z=Math.sin(t*(index?1.3:2.2)+j*1.8)*.16;}));
    bubbles.forEach((bubble,i)=>{bubble.position.y=.65+(t*(.15+i%3*.06)+i*.31)%Math.max(1,waterHeight-.3);bubble.visible=level>.12&&bubble.position.y<surface.position.y-.05;});
    surface.material.opacity=.28+Math.sin(t*1.2)*.04;
    renderer.render(scene,camera);
  }
  resize();draw(0);
  return {setLevel,dispose(){cancelAnimationFrame(frameId);observer.disconnect();renderer.dispose();}};
}
