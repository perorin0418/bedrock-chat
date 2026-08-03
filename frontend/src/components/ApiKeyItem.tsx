import React, { useState } from 'react';
import { BaseProps } from '../@types/common';
import useBotApiKey from '../hooks/useBotApiKey';
import Skeleton from './Skeleton';
import { useTranslation } from 'react-i18next';
import { PiCheckCircleBold, PiXCircleBold } from 'react-icons/pi';
import { formatDatetime } from '../utils/DateUtils';
import Button from './Button';
import ButtonCopy from './ButtonCopy';

import useBotApiSettings from '../hooks/useBotApiSettings';
import DialogConfirmDeleteApiKey from './DialogConfirmDeleteApiKey';
import { produce } from 'immer';

type Props = BaseProps & {
  botId: string;
  apiKeyId: string;
};

const ApiKeyItem: React.FC<Props> = (props) => {
  const { t } = useTranslation();
  const { botApiKey, isLoading, deleteBotApiKey } = useBotApiKey(
    props.botId,
    props.apiKeyId
  );
  const { botPublication, mutateBotPublication } = useBotApiSettings(
    props.botId
  );

  const [isHideKey, setIsHideKey] = useState(true);

  const [isOpenDialog, setIsOpenDialog] = useState(false);

  return (
    <>
      <DialogConfirmDeleteApiKey
        apiKeyTitle={botApiKey?.description ?? ''}
        isOpen={isOpenDialog}
        onDelete={() => {
          setIsOpenDialog(false);
          mutateBotPublication({
            ...botPublication!,
            apiKeyIds: produce(botPublication?.apiKeyIds ?? [], (draft) => {
              const index = draft.findIndex(
                (keyId) => keyId === props.apiKeyId
              );
              if (index > -1) {
                draft.splice(index, 1);
              }
            }),
          });
          Promise.all([deleteBotApiKey(), mutateBotPublication()]).finally(
            () => {
              mutateBotPublication();
            }
          );
        }}
        onClose={() => {
          setIsOpenDialog(false);
        }}
      />

      {isLoading ? (
        <tr>
          <td colSpan={5} className="p-1">
            <Skeleton className="h-8 w-full" />
          </td>
        </tr>
      ) : (
        <tr className="border-b border-aws-font-color-light/30 text-sm dark:border-aws-font-color-dark/30">
          <td className="p-1 align-top font-semibold">
            {botApiKey?.description}
          </td>
          <td className="p-1 align-top">
            {botApiKey?.enabled ? (
              <div className="flex items-center gap-1 text-aws-aqua">
                <PiCheckCircleBold />
                {t('bot.apiSettings.label.apiKeyDetail.active')}
              </div>
            ) : (
              <div className="flex items-center gap-1 text-red">
                <PiXCircleBold />
                {t('bot.apiSettings.label.apiKeyDetail.inactive')}
              </div>
            )}
          </td>
          <td className="p-1 align-top text-xs text-aws-font-color-light/70 dark:text-aws-font-color-dark/70">
            {botApiKey?.createdDate
              ? formatDatetime(botApiKey.createdDate)
              : ''}
          </td>
          <td className="p-1 align-top">
            <div className="flex items-center">
              <div>{isHideKey ? '***************' : botApiKey?.value}</div>
              <ButtonCopy text={botApiKey?.value ?? ''} className="-my-2" />
              <Button
                text
                className="-m-2 font-bold text-aws-sea-blue-light dark:text-aws-sea-blue-dark"
                onClick={() => {
                  setIsHideKey(!isHideKey);
                }}>
                {isHideKey
                  ? t('bot.apiSettings.button.ApiKeyShow')
                  : t('bot.apiSettings.button.ApiKeyHide')}
              </Button>
            </div>
          </td>
          <td className="p-1 text-right align-top">
            <Button
              className="bg-red"
              onClick={() => {
                setIsOpenDialog(true);
              }}>
              {t('bot.button.delete')}
            </Button>
          </td>
        </tr>
      )}
    </>
  );
};

export default ApiKeyItem;
