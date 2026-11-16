// New isolated consumer build. Never writes root dist or a signed release.
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdir,copyFile,readdir,lstat,access} from 'node:fs/promises';
import {resolve,relative,join} from 'node:path';
const require=createRequire(import.meta.url);
const {build}=require(process.env.QCHIP_NODE_MODULES?join(process.env.QCHIP_NODE_MODULES,'esbuild'):'esbuild');
const args=process.argv.slice(2);
const option=name=>{const i=args.indexOf(name);if(i<0||!args[i+1])throw Error(`Required ${name}`);return args[i+1];};
const version=option('--version'),pinPath=resolve(option('--pin'));
if(!/^\d+\.\d+\.\d+$/.test(version))throw Error('Use an explicit new desktop version');
const root=process.cwd(),vendor=resolve(root,'vendor/tec-ui');
const pinBytes=await readFile(pinPath),pin=JSON.parse(pinBytes);
if(pin.schema!==1||!pin.version||!Array.isArray(pin.files)||!pin.files.length)throw Error('Invalid consumer pin');
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const expected=new Set();
for(const entry of pin.files){
  if(typeof entry.path!=='string'||entry.path.includes('\\')||entry.path.split('/').some(x=>!x||x==='..'||x==='.'||x==='node_modules'||x==='.git'))throw Error('Unsafe vendor path');
  const file=resolve(vendor,entry.path),name=relative(vendor,file);
  if(name.startsWith('..')||resolve(file)===vendor||expected.has(entry.path))throw Error('Invalid vendor path');
  expected.add(entry.path);
  const stat=await lstat(file);
  if(!stat.isFile()||stat.isSymbolicLink())throw Error('Vendor entries must be regular files');
  const bytes=await readFile(file);
  if(bytes.length!==entry.bytes||digest(bytes)!==entry.sha256)throw Error(`Vendor hash mismatch: ${entry.path}`);
}
const actual=[];
async function walk(dir,prefix=''){
  for(const item of await readdir(dir,{withFileTypes:true})){
    const name=prefix+item.name;
    if(item.isSymbolicLink())throw Error('Vendor symlinks are forbidden');
    if(item.isDirectory())await walk(join(dir,item.name),name+'/');
    else if(item.isFile())actual.push(name);
    else throw Error('Unsupported vendor entry');
  }
}
await walk(vendor);
if(actual.length!==expected.size||actual.some(name=>!expected.has(name)))throw Error('Unpinned vendor files');
for(const name of ['components.jsx','sampling.js','measurements.js','chartMotion.js','styles.css','display.d.ts']){
  if(!expected.has(name))throw Error(`Missing public API file: ${name}`);
}
const output=resolve(root,'dist');
try{await access(output);throw Error('Output already exists; choose a fresh version');}
catch(error){if(error.code!=='ENOENT')throw error;}
await mkdir(output,{recursive:true});
const built=await build({entryPoints:['frontend/main.jsx'],bundle:true,minify:true,
  nodePaths:process.env.QCHIP_NODE_MODULES?[process.env.QCHIP_NODE_MODULES]:[],
  outfile:join(output,'app.js'),loader:{'.svg':'dataurl'},metafile:true,define:{'process.env.NODE_ENV':'"production"',
  '__QCHIP_DESKTOP_VERSION__':JSON.stringify(version)},target:['es2020'],legalComments:'eof'});
const inputs=Object.keys(built.metafile.inputs).map(name=>name.replaceAll('\\','/'));
if(!inputs.includes('vendor/tec-ui/components.jsx')||!inputs.includes('vendor/tec-ui/styles.css')){
  throw Error('Local wrapper has not integrated the public components and scoped stylesheet');
}
// Detect a partial or concurrent sync before the build can be frozen.
for(const entry of pin.files){if(digest(await readFile(join(vendor,entry.path)))!==entry.sha256)throw Error('Vendor changed during build');}
await copyFile('frontend/index.html',join(output,'index.html'));
const assets=[];
for(const name of ['index.html','app.js','app.css']){
  const bytes=await readFile(join(output,name));assets.push({path:name,bytes:bytes.length,sha256:digest(bytes)});
}
await writeFile(join(output,'tec-ui-build.json'),JSON.stringify({version,package_version:pin.version,
  pin_sha256:digest(pinBytes),vendor_files:pin.files,assets},null,2));
console.log(`Isolated Cloud Lab UI ready: ${output}`);
const gate=resolve(root,'hardware_gate_dist');await mkdir(gate,{recursive:true});
await build({entryPoints:['frontend/CloudGateWindow.jsx'],bundle:true,minify:true,
  nodePaths:process.env.QCHIP_NODE_MODULES?[process.env.QCHIP_NODE_MODULES]:[],
  outfile:join(gate,'app.js'),loader:{'.svg':'dataurl'},target:['es2020'],legalComments:'eof',
  define:{'process.env.NODE_ENV':'"production"','__QCHIP_DESKTOP_VERSION__':JSON.stringify(version)}});
await writeFile(join(gate,'index.html'),'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Qchip TEC · 网站连接</title><link rel="stylesheet" href="/app.css"><div id="root"></div><script src="/app.js"></script></html>');
