# alexa2anylist

Sync Alexa shopping list with Anylist, the hard way (thanks, Amazon!).
This will do a two-way sync between Anylist and Alexa lists. It will do it's best
to reconcile changes from either side, but if it's unable to figure it out,
the list from Anylist will overwrite the one on Alexa.

## Setup

Create a config file like called `config.json`, like this one:

```json
{
    "amazon_url": "amazon.es",
    "amazon_username": "email@address.com",
    "amazon_password": "xxxxx",
    "amazon_mfa_secret": "xxxx",
    "anylist_username": "email@address.com",
    "anylist_password": "xxxxx",
    "anylist_list_name": "Anylist List"
}
```

`amazon_url` is the Amazon site for your country (`amazon.com`, `amazon.co.uk`, `amazon.es`, ...).

Your Amazon account needs two-step verification with an authenticator app, and `amazon_mfa_secret`
is the secret key Amazon shows when you add a new authenticator app (the text version of the QR code).

On first start, alexa2anylist logs in and registers itself as a virtual Alexa device, which will
show up in your Amazon account's device list as "Name's alexa2anylist". The login is kept in
`alexa-credentials.json` next to the config file and renewed automatically, so the password is
normally only used once. To start over, delete that file.

Place the config file somewhere, like `/data/alexa2anylist/` in the example below:

Run the container like so:

```yaml
...
  alexa2anylist:
    image: alexiri/alexa2anylist:latest
    volumes:
      - /data/alexa2anylist:/config
      - /etc/localtime:/etc/localtime:ro
    environment:
      - TZ=Europe/Madrid
    restart: unless-stopped
```

`restart: unless-stopped` is recommended, so the container comes back after crashes or reboots. Upon startup, the synchronization
should continue from where it left off.

The pre-built container is at available on dockerhub: https://hub.docker.com/r/alexiri/alexa2anylist

## Credits

The Alexa login and lists API are based on https://github.com/chemelli74/aioamazondevices (used by
Home Assistant's Alexa Devices integration) and https://github.com/lonlazer/ha-alexa-todo-lists.
The interaction with Anylist is based on https://github.com/codetheweb/anylist.
