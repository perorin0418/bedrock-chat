import React from 'react';
import { BaseProps } from '../@types/common';
import Button from './Button';
import ModalDialog from './ModalDialog';
import { Trans, useTranslation } from 'react-i18next';

type Props = BaseProps & {
  isOpen: boolean;
  tokenDisplayName: string;
  onDelete: () => void;
  onClose: () => void;
};

const DialogConfirmDeleteClaudeTeamsToken: React.FC<Props> = (props) => {
  const { t } = useTranslation();
  return (
    <ModalDialog
      title={t('admin.claudeTeamsTokens.deleteDialog.title')}
      {...props}>
      <div>
        <Trans
          i18nKey="admin.claudeTeamsTokens.deleteDialog.content"
          values={{
            displayName: props.tokenDisplayName,
          }}
          components={{
            Bold: <span className="font-bold" />,
          }}
        />
      </div>

      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={props.onClose} className="p-2" outlined>
          {t('button.cancel')}
        </Button>
        <Button
          onClick={props.onDelete}
          className="bg-red p-2 text-aws-font-color-white-light dark:text-aws-font-color-white-dark">
          {t('admin.claudeTeamsTokens.button.delete')}
        </Button>
      </div>
    </ModalDialog>
  );
};

export default DialogConfirmDeleteClaudeTeamsToken;
