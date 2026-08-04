import React from 'react';
import { useTranslation } from 'react-i18next';
import { BaseProps } from '../@types/common';
import { twMerge } from 'tailwind-merge';
import useRateLimit from '../hooks/useRateLimit';
import Progress from './Progress';

type Props = BaseProps;

const toPercent = (used: number, limit: number): number => {
  if (limit <= 0) {
    return 0;
  }
  return Math.min(100, Math.round((used / limit) * 100));
};

const RateLimitStatus: React.FC<Props> = (props) => {
  const { t } = useTranslation();
  const { usageStatus, isLoading, error } = useRateLimit();

  if (isLoading || error || !usageStatus) {
    return null;
  }

  const fiveHourPercent = toPercent(
    usageStatus.fiveHour.used,
    usageStatus.fiveHour.limit
  );
  const sevenDayPercent = toPercent(
    usageStatus.sevenDay.used,
    usageStatus.sevenDay.limit
  );

  return (
    <div
      className={twMerge(
        'flex flex-col gap-1.5 text-aws-font-color-white-light dark:text-aws-font-color-white-dark',
        props.className
      )}>
      <div>
        <div className="flex justify-between text-xs">
          <span>{t('app.usageStatus.fiveHour')}</span>
          <span>{fiveHourPercent}%</span>
        </div>
        <Progress thin progress={fiveHourPercent} />
      </div>
      <div>
        <div className="flex justify-between text-xs">
          <span>{t('app.usageStatus.sevenDay')}</span>
          <span>{sevenDayPercent}%</span>
        </div>
        <Progress thin progress={sevenDayPercent} />
      </div>
    </div>
  );
};

export default RateLimitStatus;
