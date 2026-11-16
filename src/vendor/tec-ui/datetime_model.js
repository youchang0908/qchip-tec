const pad = (value, width = 2) => String(value).padStart(width, '0');

// Calendar arithmetic uses UTC fields; these strings represent wall time in UTC+8.
export function calendarDays(year, month) {
  const date = new Date(Date.UTC(year, month - 1, 1));
  date.setUTCDate(1 - (date.getUTCDay() + 6) % 7);
  return Array.from({length: 42}, () => {
    const key = date.toISOString().slice(0, 10);
    const day = {key, day: date.getUTCDate(), current: date.getUTCMonth() === month - 1};
    date.setUTCDate(date.getUTCDate() + 1);
    return day;
  });
}

export function moveMonth(year, month, step) {
  const date = new Date(Date.UTC(year, month - 1 + step, 1));
  return {year: date.getUTCFullYear(), month: date.getUTCMonth() + 1};
}

export function replaceDate(value, date) {
  return date + value.slice(10);
}

export function replaceTime(value, part, next) {
  const bounds = {hour: [11, 13, 23], minute: [14, 16, 59], second: [17, 19, 59], millisecond: [20, 23, 999]};
  const [from, to, max] = bounds[part];
  const number = Math.min(max, Math.max(0, Math.trunc(Number(next) || 0)));
  return value.slice(0, from) + pad(number, to - from) + value.slice(to);
}
