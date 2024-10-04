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

Place it somewhere, like `/data/alexa2anylist` in the example below:

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

## Credits

Based on https://github.com/madmachinations/home-assistant-alexa-shopping-list for the
handling of Alexa, and on https://github.com/codetheweb/anylist for the interaction with
Anylist.
