/* Плоские иконки в стиле референса (палитра C задаётся в index.html). */
const spark=(x,y,s,c)=>`<path transform="translate(${x} ${y}) scale(${s})" d="M0-10C1.2-2 2-1.2 10 0 2 1.2 1.2 2 0 10-1.2 2-2 1.2-10 0-2-1.2-1.2-2 0-10Z" fill="${c}"/>`;
const sparkO=(x,y,s,c)=>`<path transform="translate(${x} ${y}) scale(${s})" d="M0-10C1.2-2 2-1.2 10 0 2 1.2 1.2 2 0 10-1.2 2-2 1.2-10 0-2-1.2-1.2-2 0-10Z" fill="none" stroke="${c}" stroke-width="2.2" stroke-linejoin="round"/>`;
const dia=(x,y,s,c)=>`<path transform="translate(${x} ${y}) scale(${s})" d="M0-10 5 0 0 10-5 0Z" fill="${c}"/>`;
const I={
  mic:()=>`<svg viewBox="0 0 120 120">${sparkO(20,26,1.2,C.ink)}${dia(104,58,1,C.green)}${spark(96,20,.7,C.yellow)}
    <rect x="44" y="14" width="34" height="58" rx="17" fill="${C.blue}"/><rect x="50" y="24" width="22" height="4" rx="2" fill="#fff" opacity=".6"/><rect x="50" y="34" width="22" height="4" rx="2" fill="#fff" opacity=".6"/><rect x="50" y="44" width="22" height="4" rx="2" fill="#fff" opacity=".6"/>
    <path d="M34 58Q34 88 61 88 88 88 88 58" stroke="${C.red}" stroke-width="7" fill="none" stroke-linecap="round"/>
    <rect x="57" y="88" width="8" height="16" fill="${C.red}"/><rect x="40" y="102" width="42" height="10" rx="5" fill="${C.yellow}"/></svg>`,
  eye:()=>`<svg viewBox="0 0 120 120">${spark(16,20,.9,C.yellow)}${dia(106,98,1,C.pink)}
    <rect x="12" y="24" width="96" height="68" rx="8" fill="${C.ink}"/><rect x="18" y="30" width="84" height="56" rx="4" fill="${C.blue}"/>
    <path d="M28 58Q60 30 92 58 60 86 28 58Z" fill="#fff"/><circle cx="60" cy="58" r="15" fill="${C.pink}"/><circle cx="60" cy="58" r="7" fill="${C.ink}"/><circle cx="64" cy="54" r="3" fill="#fff"/>
    <rect x="50" y="92" width="20" height="10" fill="${C.ink}"/><rect x="36" y="100" width="48" height="8" rx="4" fill="${C.red}"/></svg>`,
  books:()=>`<svg viewBox="0 0 120 120">${spark(100,22,1,'#fff')}${dia(18,40,.9,C.pink)}
    <rect x="18" y="80" width="84" height="18" rx="4" fill="${C.blue}"/><rect x="24" y="84" width="70" height="4" fill="#fff" opacity=".5"/>
    <rect x="26" y="62" width="76" height="18" rx="4" fill="${C.yellow}"/><rect x="30" y="66" width="30" height="10" fill="${C.green}"/>
    <rect x="14" y="44" width="80" height="18" rx="4" fill="${C.red}"/><rect x="20" y="48" width="66" height="4" fill="#fff" opacity=".5"/>
    <rect x="30" y="26" width="66" height="18" rx="4" fill="${C.pink}"/><rect x="36" y="30" width="16" height="10" fill="${C.ink}"/></svg>`,
  cal:(n='14')=>`<svg viewBox="0 0 140 110">${spark(18,22,1,C.blue)}${dia(128,30,1.1,C.green)}
    <circle cx="36" cy="64" r="26" fill="#fff" stroke="${C.red}" stroke-width="5"/><path d="M36 48V64L48 70" stroke="${C.ink}" stroke-width="4" fill="none" stroke-linecap="round"/>
    <rect x="58" y="36" width="64" height="58" rx="6" fill="${C.pink}"/><rect x="58" y="36" width="64" height="16" rx="6" fill="${C.blue}"/>
    <circle cx="72" cy="36" r="4" fill="none" stroke="${C.ink}" stroke-width="2.5"/><circle cx="90" cy="36" r="4" fill="none" stroke="${C.ink}" stroke-width="2.5"/><circle cx="108" cy="36" r="4" fill="none" stroke="${C.ink}" stroke-width="2.5"/>
    <text x="90" y="84" text-anchor="middle" font-family="Onest" font-weight="800" font-size="26" fill="#fff">${n}</text>
    <path d="M118 60 134 52 126 66 138 70 122 80Z" fill="${C.red}"/><path d="M122 66 130 62 126 70Z" fill="${C.yellow}"/></svg>`,
  music:()=>`<svg viewBox="0 0 120 120">${spark(100,20,1,C.yellow)}${dia(16,30,1,C.green)}
    <path d="M26 70Q26 26 60 26 94 26 94 70" stroke="${C.ink}" stroke-width="9" fill="none"/>
    <rect x="16" y="62" width="24" height="38" rx="10" fill="${C.red}"/><rect x="80" y="62" width="24" height="38" rx="10" fill="${C.red}"/>
    <rect x="22" y="68" width="8" height="26" rx="4" fill="${C.pink}"/><rect x="90" y="68" width="8" height="26" rx="4" fill="${C.pink}"/>
    <path d="M56 50V84" stroke="${C.blue}" stroke-width="5"/><path d="M56 50 74 44V56L56 62Z" fill="${C.blue}"/><circle cx="50" cy="86" r="8" fill="${C.blue}"/></svg>`,
  weather:()=>`<svg viewBox="0 0 120 120">${spark(18,22,.9,C.pink)}${dia(104,98,1,C.green)}
    <circle cx="74" cy="44" r="24" fill="${C.yellow}"/>
    ${[0,45,90,135,180,225,270,315].map(a=>`<rect x="72" y="8" width="4" height="10" rx="2" fill="${C.yellow}" transform="rotate(${a} 74 44)"/>`).join('')}
    <path d="M30 94Q14 94 16 78 18 64 34 66 38 50 56 52 72 54 72 70 88 68 90 82 90 94 76 94Z" fill="#fff" stroke="${C.ink}" stroke-width="3"/>
    <path d="M40 104 36 112M58 104 54 112" stroke="${C.blue}" stroke-width="4" stroke-linecap="round"/></svg>`,
  laptop:()=>`<svg viewBox="0 0 120 120">${sparkO(18,20,1.1,C.ink)}${dia(106,24,1,C.green)}
    <rect x="22" y="28" width="76" height="52" rx="6" fill="${C.ink}"/><rect x="27" y="33" width="66" height="42" rx="3" fill="${C.blue}"/>
    <rect x="33" y="40" width="30" height="20" rx="3" fill="${C.yellow}"/><rect x="33" y="40" width="30" height="5" rx="2" fill="${C.red}"/>
    <rect x="58" y="52" width="28" height="18" rx="3" fill="${C.pink}"/><path d="M64 60h16M64 65h10" stroke="#fff" stroke-width="2"/>
    <path d="M12 84H108L100 94H20Z" fill="#cfd1da"/><rect x="50" y="84" width="20" height="4" rx="2" fill="#9ea0ad"/></svg>`,
  plane:()=>`<svg viewBox="0 0 120 120">${spark(100,96,1,C.yellow)}${dia(18,24,1,C.pink)}
    <circle cx="60" cy="60" r="44" fill="${C.blue}"/><path d="M32 58 88 36 78 88 62 74 54 86 52 68Z" fill="#fff"/><path d="M52 68 84 42 62 74Z" fill="#cfe0ff"/></svg>`,
  bulb:()=>`<svg viewBox="0 0 120 120">${sparkO(24,24,1,C.ink)}${dia(96,92,1,C.red)}${spark(98,26,.7,C.pink)}
    <circle cx="60" cy="52" r="28" fill="${C.yellow}"/><path d="M50 50 56 58 62 44 68 56" stroke="${C.ink}" stroke-width="3" fill="none"/>
    <rect x="48" y="76" width="24" height="10" fill="${C.blue}"/><rect x="50" y="86" width="20" height="8" rx="3" fill="${C.ink}"/></svg>`,
  bell:()=>`<svg viewBox="0 0 120 120">${spark(98,22,1,C.yellow)}${dia(18,90,1,C.green)}
    <path d="M60 18Q88 18 88 52V74L98 86H22L32 74V52Q32 18 60 18Z" fill="${C.red}"/><rect x="40" y="36" width="10" height="26" rx="5" fill="#fff" opacity=".45"/>
    <circle cx="60" cy="94" r="10" fill="${C.yellow}"/><circle cx="88" cy="30" r="12" fill="${C.pink}"/><text x="88" y="35" text-anchor="middle" font-family="Onest" font-weight="800" font-size="14" fill="#fff">3</text></svg>`,
  dumbbell:()=>`<svg viewBox="0 0 120 120">${spark(100,24,1,C.blue)}${dia(20,94,1,C.pink)}
    <rect x="30" y="54" width="60" height="12" rx="4" fill="${C.ink}"/><rect x="18" y="34" width="16" height="52" rx="5" fill="${C.red}"/><rect x="86" y="34" width="16" height="52" rx="5" fill="${C.red}"/>
    <rect x="8" y="44" width="12" height="32" rx="4" fill="${C.yellow}"/><rect x="100" y="44" width="12" height="32" rx="4" fill="${C.yellow}"/></svg>`,
  drop:()=>`<svg viewBox="0 0 120 120">${spark(98,26,.9,C.yellow)}
    <path d="M60 14Q92 56 92 76 92 104 60 104 28 104 28 76 28 56 60 14Z" fill="${C.blue}"/><path d="M44 74Q44 90 58 92" stroke="#fff" stroke-width="5" fill="none" stroke-linecap="round"/></svg>`,
  globe:()=>`<svg viewBox="0 0 120 120">${spark(20,22,1,C.pink)}${dia(104,96,1,C.green)}
    <rect x="12" y="24" width="56" height="42" rx="12" fill="${C.yellow}"/><path d="M26 66 22 80 38 66Z" fill="${C.yellow}"/><text x="40" y="55" text-anchor="middle" font-family="Onest" font-weight="800" font-size="26" fill="${C.ink}">A</text>
    <rect x="52" y="52" width="56" height="42" rx="12" fill="${C.red}"/><path d="M94 94 98 108 82 94Z" fill="${C.red}"/><text x="80" y="83" text-anchor="middle" font-family="Onest" font-weight="800" font-size="26" fill="#fff">Я</text></svg>`,
  news:()=>`<svg viewBox="0 0 120 120">${spark(102,22,1,C.red)}
    <rect x="18" y="22" width="80" height="80" rx="6" fill="#fff" stroke="${C.ink}" stroke-width="3"/><rect x="26" y="30" width="64" height="14" fill="${C.ink}"/>
    <rect x="26" y="50" width="28" height="26" fill="${C.pink}"/><path d="M60 52h30M60 60h30M60 68h22M26 84h64M26 92h48" stroke="${C.blue}" stroke-width="4"/></svg>`,
  brain:()=>`<svg viewBox="0 0 120 120">${spark(100,22,1,C.yellow)}${dia(16,96,1,C.blue)}
    <path d="M60 22Q40 14 30 30 14 34 18 52 8 66 22 78 22 96 42 96 52 106 60 98Z" fill="${C.pink}"/>
    <path d="M60 22Q80 14 90 30 106 34 102 52 112 66 98 78 98 96 78 96 68 106 60 98Z" fill="${C.red}"/>
    <path d="M60 24V98M36 44Q46 48 44 60M84 44Q74 48 76 60M34 76Q44 72 50 80M86 76Q76 72 70 80" stroke="${C.ink}" stroke-width="3" fill="none" stroke-linecap="round"/></svg>`,
  cocktail:()=>`<svg viewBox="0 0 120 120">${spark(100,24,1,C.green)}${dia(22,92,1.1,C.red)}
    <path d="M22 26H98L60 64Z" fill="#fff" stroke="#fff" stroke-width="3"/><path d="M36 40H84L60 64Z" fill="${C.red}"/>
    <rect x="57" y="62" width="6" height="30" fill="#fff"/><rect x="42" y="90" width="36" height="6" rx="3" fill="#fff"/>
    <circle cx="74" cy="30" r="7" fill="${C.green}"/><path d="M74 30 90 12" stroke="${C.yellow}" stroke-width="3"/></svg>`,
  rocket:()=>`<svg viewBox="0 0 120 120">${spark(20,24,1,C.yellow)}${dia(100,30,1,C.pink)}
    <path d="M60 10Q84 30 80 74H40Q36 30 60 10Z" fill="#fff" stroke="${C.ink}" stroke-width="3"/><circle cx="60" cy="42" r="9" fill="${C.blue}"/>
    <path d="M40 60 24 82 40 78ZM80 60 96 82 80 78Z" fill="${C.red}"/><path d="M46 76Q60 118 74 76Z" fill="${C.yellow}"/><path d="M52 76Q60 100 68 76Z" fill="${C.red}"/></svg>`,
  lock:()=>`<svg viewBox="0 0 60 60"><path d="M20 26V18Q20 8 30 8 40 8 40 18V26" stroke="${C.ink}" stroke-width="5" fill="none"/><rect x="12" y="26" width="36" height="28" rx="5" fill="${C.pink}"/><rect x="12" y="26" width="18" height="28" rx="5" fill="${C.blue}"/></svg>`,
  cam:()=>`<svg viewBox="0 0 60 60"><rect x="6" y="18" width="48" height="34" rx="6" fill="${C.red}"/><rect x="20" y="10" width="20" height="10" rx="3" fill="${C.red}"/><circle cx="30" cy="35" r="11" fill="#fff"/><circle cx="30" cy="35" r="6" fill="${C.blue}"/></svg>`,
  shot:()=>`<svg viewBox="0 0 60 60"><rect x="8" y="12" width="44" height="32" rx="4" fill="${C.blue}"/><rect x="14" y="18" width="18" height="12" fill="${C.yellow}"/><path d="M4 22V8H18M56 22V8H42M4 40V54H18M56 40V54H42" stroke="${C.ink}" stroke-width="4" fill="none"/></svg>`,
  vol:()=>`<svg viewBox="0 0 60 60"><path d="M8 22H20L34 10V50L20 38H8Z" fill="${C.yellow}"/><path d="M42 20Q50 30 42 40M48 14Q60 30 48 46" stroke="${C.green}" stroke-width="4" fill="none" stroke-linecap="round"/></svg>`,
};
const svg=(name,...a)=>I[name](...a);

/* extra icons for v2 */
Object.assign(I,{
  film:()=>`<svg viewBox="0 0 120 120">${spark(100,22,1,C.yellow)}<rect x="16" y="30" width="88" height="62" rx="8" fill="${C.ink}"/>${[0,1,2,3,4,5].map(i=>`<rect x="${22+i*15}" y="34" width="8" height="7" rx="2" fill="#fff"/><rect x="${22+i*15}" y="81" width="8" height="7" rx="2" fill="#fff"/>`).join('')}<rect x="22" y="46" width="76" height="30" fill="${C.red}"/><path d="M54 52 70 61 54 70Z" fill="#fff"/></svg>`,
  yt:()=>`<svg viewBox="0 0 120 120">${dia(18,24,1,C.pink)}<rect x="14" y="30" width="92" height="62" rx="18" fill="${C.red}"/><path d="M50 46 76 61 50 76Z" fill="#fff"/></svg>`,
  folder:()=>`<svg viewBox="0 0 120 120">${spark(100,24,1,C.pink)}<path d="M14 34h34l8 10h50v52H14Z" fill="${C.yellow}"/><rect x="14" y="52" width="92" height="44" rx="4" fill="#ffc400"/><rect x="26" y="40" width="40" height="30" fill="#fff" transform="rotate(-8 46 55)"/></svg>`,
  win:()=>`<svg viewBox="0 0 120 120">${spark(100,20,1,C.yellow)}<rect x="18" y="22" width="84" height="64" rx="6" fill="${C.blue}"/><rect x="18" y="22" width="84" height="12" rx="6" fill="${C.ink}"/><rect x="28" y="44" width="30" height="30" rx="3" fill="#fff"/><rect x="64" y="44" width="28" height="12" rx="3" fill="${C.pink}"/><rect x="64" y="62" width="28" height="12" rx="3" fill="${C.yellow}"/><rect x="40" y="92" width="40" height="8" rx="4" fill="${C.ink}"/></svg>`,
  ball:()=>`<svg viewBox="0 0 120 120">${spark(100,22,1,C.yellow)}<circle cx="60" cy="62" r="40" fill="#fff" stroke="${C.ink}" stroke-width="4"/><path d="M60 44 76 56 70 76H50L44 56Z" fill="${C.ink}"/><path d="M60 44V24M76 56 96 50M70 76 82 94M50 76 38 94M44 56 24 50" stroke="${C.ink}" stroke-width="4"/></svg>`,
  phone:()=>`<svg viewBox="0 0 120 120">${dia(100,24,1,C.green)}<path d="M34 20h18l8 24-12 8q8 18 22 26l8-12 24 8v18q0 10-10 10Q30 102 24 30q0-10 10-10Z" fill="${C.green}"/></svg>`,
  alarm:()=>`<svg viewBox="0 0 120 120">${spark(100,20,1,C.pink)}<circle cx="60" cy="64" r="36" fill="#fff" stroke="${C.red}" stroke-width="7"/><path d="M60 42V64L76 74" stroke="${C.ink}" stroke-width="6" fill="none" stroke-linecap="round"/><circle cx="28" cy="30" r="12" fill="${C.red}"/><circle cx="92" cy="30" r="12" fill="${C.red}"/></svg>`,
  cup:()=>`<svg viewBox="0 0 120 120">${spark(98,22,1,C.green)}<path d="M26 44h58v28q0 24-29 24T26 72Z" fill="${C.pink}"/><path d="M84 52q18 0 18 12t-18 12" stroke="${C.pink}" stroke-width="7" fill="none"/><path d="M44 18q-6 10 0 18M58 18q-6 10 0 18" stroke="${C.ink}" stroke-width="4" fill="none" stroke-linecap="round"/></svg>`,
  voice:()=>`<svg viewBox="0 0 120 120">${spark(100,22,1,C.yellow)}${[18,30,42,54,66,78,90,102].map((x,i)=>`<rect x="${x-4}" y="${60-[10,22,34,46,30,40,20,12][i]}" width="8" height="${2*[10,22,34,46,30,40,20,12][i]}" rx="4" fill="${[C.blue,C.pink,C.red,C.blue,C.yellow,C.red,C.pink,C.blue][i]}"/>`).join('')}</svg>`,
  cmd:()=>`<svg viewBox="0 0 120 120">${spark(100,22,1,C.pink)}<path d="M66 10 26 68h30l-8 42 44-62H60Z" fill="${C.yellow}" stroke="${C.ink}" stroke-width="3" stroke-linejoin="round"/></svg>`,
});
