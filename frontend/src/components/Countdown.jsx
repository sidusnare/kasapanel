// SPDX-FileCopyrightText: 2026 Kasa Panel contributors
//
// SPDX-License-Identifier: GPL-3.0-or-later
//
// SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
// see docs/ai-bom.spdx.json for the AI usage declaration.

import React, {useEffect, useState} from 'react';

// A live countdown. It ticks once a second near the end and once a
// minute otherwise, because nobody watches the seconds drain out of
// three months.

function parts(seconds) {
  const whole = Math.floor(Math.abs(seconds));
  return {
    days: Math.floor(whole / 86400),
    hours: Math.floor((whole % 86400) / 3600),
    minutes: Math.floor((whole % 3600) / 60),
    seconds: whole % 60,
  };
}

export function describeGap(seconds) {
  const {days, hours, minutes, seconds: rest} = parts(seconds);
  if (days > 0) {
    return `${days}d ${hours}h ${minutes}m`;
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m ${rest}s`;
  }
  return `${minutes}m ${rest}s`;
}

export default function Countdown({until, className}) {
  const target = until ? new Date(until).getTime() : 0;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!target) {
      return undefined;
    }
    const left = target - Date.now();
    const every = Math.abs(left) < 48 * 3600 * 1000 ? 1000 : 60000;
    const timer = window.setInterval(() => setNow(Date.now()), every);
    return () => window.clearInterval(timer);
  }, [target]);

  if (!target || Number.isNaN(target)) {
    return <span className={className}>unknown</span>;
  }
  const left = (target - now) / 1000;
  if (left <= 0) {
    return (
      <span className={`${className || ''} state-word fault`}>
        expired {describeGap(left)} ago
      </span>
    );
  }
  const tone = left < 48 * 3600 ? 'fault' : left < 14 * 86400 ? 'warn' : '';
  return (
    <span className={`${className || ''} ${tone ? `state-word ${tone}` : ''}`}>
      {describeGap(left)}
    </span>
  );
}
