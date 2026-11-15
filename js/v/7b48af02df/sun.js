// Solar position for the sun-study slider. Low-precision almanac formulas
// (good to a few hundredths of a degree for 1950-2050), no dependencies.

const RAD = Math.PI / 180;

/** Sun altitude and azimuth (radians; azimuth clockwise from true north). */
export function sunPosition(dateUTC, latDeg, lonDeg) {
  const jd = dateUTC.getTime() / 86400000 + 2440587.5;
  const n = jd - 2451545.0;
  const L = (280.460 + 0.9856474 * n) % 360;
  const g = ((357.528 + 0.9856003 * n) % 360) * RAD;
  const lambda = (L + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * RAD;
  const eps = (23.439 - 0.0000004 * n) * RAD;
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lambda), Math.cos(lambda));
  const dec = Math.asin(Math.sin(eps) * Math.sin(lambda));
  const gmst = (18.697374558 + 24.06570982441908 * n) % 24;
  const H = (gmst * 15 + lonDeg) * RAD - ra;
  const lat = latDeg * RAD;
  const alt = Math.asin(Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(H));
  const azSouth = Math.atan2(Math.sin(H), Math.cos(H) * Math.sin(lat) - Math.tan(dec) * Math.cos(lat));
  return { alt, az: (azSouth + Math.PI + 2 * Math.PI) % (2 * Math.PI), dec };
}

/** US daylight-saving rule (second Sunday of March to first Sunday of November). */
function isDST(year, month, day) {
  const nthSunday = (m, nth) => {
    const first = new Date(Date.UTC(year, m, 1)).getUTCDay();
    return 1 + ((7 - first) % 7) + 7 * (nth - 1);
  };
  const start = nthSunday(2, 2);
  const end = nthSunday(10, 1);
  if (month > 2 && month < 10) return true;
  if (month === 2) return day >= start;
  if (month === 10) return day < end;
  return false;
}

/** Eastern-time wall clock (day of year + minutes after midnight) -> UTC Date. */
export function easternToUTC(year, dayOfYear, minutes) {
  const d = new Date(Date.UTC(year, 0, dayOfYear));
  const dst = isDST(year, d.getUTCMonth(), d.getUTCDate());
  const offsetH = dst ? 4 : 5;
  return { date: new Date(d.getTime() + (minutes + offsetH * 60) * 60000), dst };
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function formatDay(year, dayOfYear) {
  const d = new Date(Date.UTC(year, 0, dayOfYear));
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

export function formatClock(minutes) {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  const hh = ((h + 11) % 12) + 1;
  return `${hh}:${String(m).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
}

/** Sunrise and sunset in local minutes for the day (scan at 2-minute steps). */
export function daylight(year, dayOfYear, lat, lon) {
  let rise = null;
  let set = null;
  let prev = null;
  for (let m = 180; m <= 1380; m += 2) {
    const { date } = easternToUTC(year, dayOfYear, m);
    const up = sunPosition(date, lat, lon).alt > -0.0145;
    if (prev !== null && up && !prev) rise = m;
    if (prev !== null && !up && prev) set = m;
    prev = up;
  }
  return { rise, set };
}

export function compassPoint(azRad) {
  const names = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  return names[Math.round(azRad / (Math.PI / 8)) % 16];
}
