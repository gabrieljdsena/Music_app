create database if not exists Music_Player;

use Music_Player;

create table if not exists Songs(
    id bigint not null PRIMARY KEY,
    title varchar(255) not null,
    album varchar(255),
    file_name varchar(255),
    album_id bigint,
    genre varchar(255),
    FOREIGN KEY (album_id) REFERENCES Albums(id)
);

create table if not exists Albums(
    id bigint not null PRIMARY KEY,
    release_year int,
    title varchar(255) not null,
    thumbnail varchar(255),
    artist varchar(255)
);

create table if not exists Song_Playlist(
    id bigint not null PRIMARY KEY,
    song_id bigint not null,
    playlist_id bigint not null,
    FOREIGN KEY (song_id) REFERENCES Songs(id),
    FOREIGN KEY (playlist_id) REFERENCES Playlists(id)
);

create table if not exists Playlists(
    id bigint not null PRIMARY KEY,
    title varchar(255) not null,
    thumbnail varchar(255),
    description text
);

create table if not exists Sessions(
    current_song_id bigint not null,
    current_time bigint not null,
    current_tab varchar(255) not null,
    window_size varchar(9) not null,
    volume int not null,
    FOREIGN KEY (current_song_id) REFERENCES Songs(id)
);

create table if not exists Settings(
    standardize_volume boolean not null
);