import React, { useEffect, useState } from 'react';
import { BaseProps, DrawerOptions } from '../@types/common';
import Button from './Button';
import ModalDialog from './ModalDialog';
import { useTranslation } from 'react-i18next';
import Toggle from './Toggle';

type Props = BaseProps & {
  isOpen: boolean;
  drawerOptions: DrawerOptions;
  onChangeDrawerOptions: (drawerOptions: DrawerOptions) => void;
  onClose: () => void;
};

const DialogDrawerOptions: React.FC<Props> = (props) => {
  const { t } = useTranslation();

  const [showMyBots, setShowMyBots] = useState(true);
  const [showPinnedBots, setShowPinnedBots] = useState(true);

  useEffect(() => {
    if (props.isOpen) {
      setShowMyBots(props.drawerOptions.show.myBots);
      setShowPinnedBots(props.drawerOptions.show.pinnedBots);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.isOpen]);

  return (
    <ModalDialog {...props} title={t('drawerOptionsDialog.title')}>
      <div className="flex flex-col gap-3">
        <div>
          <div className="text-base font-bold">
            {t('drawerOptionsDialog.label.visibility')}
          </div>
          <div className="ml-3 mt-1 flex flex-col gap-1">
            <Toggle
              label={t('app.myBots')}
              value={showMyBots}
              onChange={setShowMyBots}
            />
            <Toggle
              label={t('app.pinnedBots')}
              value={showPinnedBots}
              onChange={setShowPinnedBots}
            />
          </div>
        </div>
      </div>

      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={props.onClose} className="p-2" outlined>
          {t('button.cancel')}
        </Button>
        <Button
          onClick={() => {
            props.onChangeDrawerOptions({
              show: {
                myBots: showMyBots,
                pinnedBots: showPinnedBots,
              },
            });
          }}
          className="p-2">
          {t('button.ok')}
        </Button>
      </div>
    </ModalDialog>
  );
};

export default DialogDrawerOptions;
