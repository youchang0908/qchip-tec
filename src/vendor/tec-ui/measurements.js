// Display formatting only: stored samples retain their original numeric values.
// The 250 mW knee is a display scale, not an instrument rating or limit.
export function thermalState(current, voltage, output) {
  const direction = output === true && Number.isFinite(current) && current !== 0
    ? current < 0 ? 'heating' : 'cooling' : null;
  const product = Number.isFinite(current) && Number.isFinite(voltage) ? current * voltage : NaN;
  const watts = Number.isFinite(product) ? Math.abs(product) : null;
  const level=direction && watts > 0 ? watts / (watts + .25) : 0;
  return {direction, watts, level, fill:level>0?3+15*level:0, edge:level>0?10+28*level:0};
}

export function formatPower(watts) {
  if (!Number.isFinite(watts)) return {value:'—', unit:'W'};
  const milli = Math.abs(watts) < 1;
  return {value:(milli ? watts * 1000 : watts).toPrecision(4), unit:milli ? 'mW' : 'W'};
}

export function temperatureScale(values, relative = false) {
  const finite = values.filter(Number.isFinite);
  const low = finite.length ? Math.min(...finite) : relative ? 0 : 20;
  const high = finite.length ? Math.max(...finite) : low;
  const middle = (low + high) / 2;
  // Numerical floor prevents a flat trace from producing a zero-width domain.
  const span = Math.max(high - low, relative ? .1 : .0001);
  const paddedLow = middle - span * .65, paddedHigh = middle + span * .65;
  const rawStep = (paddedHigh - paddedLow) / 5;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const step = [1, 2, 2.5, 5, 10].find(n => n * magnitude >= rawStep) * magnitude;
  const min = Math.floor(paddedLow / step) * step;
  const max = Math.ceil(paddedHigh / step) * step;
  let precision = 0;
  while (precision < 6 && Math.abs(step * 10 ** precision - Math.round(step * 10 ** precision)) > 1e-7) precision++;
  const ticks = Array.from({length:Math.round((max-min)/step)+1}, (_,i) => min+i*step);
  return {min, max, precision, ticks};
}
