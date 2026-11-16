import test from 'node:test';
import assert from 'node:assert/strict';
import {formatPower, temperatureScale} from './measurements.js';

test('power switches at 1 W and retains four significant figures below it', () => {
  assert.deepEqual(formatPower(.00112), {value:'1.120',unit:'mW'});
  assert.deepEqual(formatPower(.0000001234), {value:'0.0001234',unit:'mW'});
  assert.deepEqual(formatPower(.18928), {value:'189.3',unit:'mW'});
  assert.deepEqual(formatPower(1), {value:'1.000',unit:'W'});
  assert.deepEqual(formatPower(-.00112), {value:'-1.120',unit:'mW'});
  assert.deepEqual(formatPower(0), {value:'0.000',unit:'mW'});
});

test('sub-mK variations use the visible data range and distinct tick labels', () => {
  const scale=temperatureScale([19.9997,20.0001,20.0003]);
  assert.ok(scale.min<19.9997 && scale.max>20.0003);
  assert.ok(scale.max-scale.min<.002);
  const labels=scale.ticks.map(t=>t.toFixed(scale.precision));
  assert.equal(new Set(labels).size,labels.length);
});

test('scale expands for a transient, shrinks when it leaves, and handles flat/empty data', () => {
  const wide=temperatureScale([18,19,20]), narrow=temperatureScale([19.9999,20]);
  assert.ok(wide.min<18 && wide.max>20);
  assert.ok(narrow.max-narrow.min<.001);
  for(const values of [[],[20,20],[NaN,20,Infinity]]) {
    const s=temperatureScale(values);
    assert.ok(Number.isFinite(s.min) && Number.isFinite(s.max) && s.max>s.min);
  }
});

test('absolute and relative temperature axes use equivalent physical ranges', () => {
  const c=temperatureScale([-.0003,.0002]), mK=temperatureScale([-.3,.2],true);
  assert.ok(Math.abs(c.min*1000-mK.min)<1e-9);
  assert.ok(Math.abs(c.max*1000-mK.max)<1e-9);
});
