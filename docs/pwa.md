# Install BigPicks and enable weekly pick alerts

Open BigPicks over HTTPS. On Android, choose **Install BigPicks** in Account when offered or use the browser’s install menu. On iPhone or iPad (iOS/iPadOS 16.4 or later), use **Share → Add to Home Screen** and open BigPicks from that icon. In **Account → Weekly pick alerts**, select **Turn on alerts** and allow notifications. Each browser or installed app opts in separately.

The first time a manager saves games for the week, BigPicks queues an alert for each opted-in device belonging to an active league member. The alert opens that league’s Picks page. Members can turn alerts off in Account. Signing out of the session where alerts were enabled also disconnects that device.

## Production setup

1. Install the updated requirements in the application virtual environment. Run the usual Django deployment commands from the directory containing `manage.py`.

   ```sh
   python manage.py migrate
   python manage.py collectstatic --noinput
   ```

2. As the service user, with the virtual environment active, create the Web Push signing key once. Keep this directory outside the checkout and back it up. Existing browser subscriptions depend on this key.

   ```sh
   umask 077
   mkdir -p "$HOME/.config/bigpicks/webpush"
   cd "$HOME/.config/bigpicks/webpush"
   test -s private_key.pem || vapid --gen
   vapid --applicationServerKey
   ```

3. Add the following to the environment shared by Django and Celery. Use the absolute path to the file above and copy the public value printed after `Application Server Key =`.

   ```dotenv
   WEB_PUSH_PRIVATE_KEY=/home/pmojo375/.config/bigpicks/webpush/private_key.pem
   WEB_PUSH_PUBLIC_KEY=<public application server key>
   WEB_PUSH_SUBJECT=mailto:support@bigpicks.app
   ```

4. Restart Gunicorn, the Celery worker, and Celery Beat. The existing worker must consume the default `celery` queue. Beat checks for pending alerts once a minute. The app installs even when push credentials are absent.

To check delivery, opt in on a device in a test league, close the app, and have its manager save that league’s first games for the current picks week. Tap the notification and verify the league on the Picks page.
