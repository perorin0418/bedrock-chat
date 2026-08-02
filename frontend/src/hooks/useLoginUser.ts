import { fetchAuthSession } from 'aws-amplify/auth';
import { useEffect, useMemo, useState } from 'react';
import useSWR from 'swr';

const GROUP_PUBLISH_ALLOWED = 'PublishAllowed';
const GROUP_CREATING_BOT_ALLOWED = 'CreatingBotAllowed';
const GROUP_ADMIN = 'Admin';

const useLoginUser = () => {
  const [isAllowApiSettings, setIsAllowApiSettings] = useState(false);
  const [isAllowCreatingBot, setIsAllowCreatingBot] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);

  const { data: session } = useSWR('current-session', () => fetchAuthSession());

  const userName = useMemo(() => {
    return session?.tokens?.idToken?.payload?.['email']?.toString() ?? '';
  }, [session?.tokens?.idToken?.payload]);

  const groups = useMemo(() => {
    return session?.tokens?.idToken?.payload?.['cognito:groups'];
  }, [session?.tokens?.idToken?.payload]);

  const userId = useMemo(() => {
    return session?.tokens?.idToken?.payload?.sub;
  }, [session]);

  useEffect(() => {
    if (Array.isArray(groups)) {
      setIsAllowApiSettings(
        groups.some(
          (group) => group === GROUP_PUBLISH_ALLOWED || group === GROUP_ADMIN
        )
      );
      setIsAllowCreatingBot(
        groups.some(
          (group) =>
            group === GROUP_CREATING_BOT_ALLOWED || group === GROUP_ADMIN
        )
      );
      setIsAdmin(groups.some((group) => group === GROUP_ADMIN));
    } else {
      setIsAllowApiSettings(false);
      setIsAllowCreatingBot(false);
      setIsAdmin(false);
    }
  }, [groups, session]);

  return {
    isAllowApiSettings,
    isAllowCreatingBot,
    isAdmin,
    isLoading: session === undefined,
    userGroups: Array.isArray(groups)
      ? groups.map((group) => group?.toString() ?? '')
      : [],
    userName,
    userId,
  };
};

export default useLoginUser;
