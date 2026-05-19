create table if not exists Songs(
    file varchar(255) PRIMARY KEY,
    downloaded_link varchar(255) null,
    title varchar(255) not null
);

create table if not exists Playlists(
    id bigint not null PRIMARY KEY,
    title varchar(255) not null,
    thumbnail varchar(255) null,
    description text
);

create table if not exists Song_Playlist(
    id bigint not null PRIMARY KEY,
    song_file varchar(255) not null,
    playlist_id bigint not null,
    FOREIGN KEY (song_file) REFERENCES Songs(file),
    FOREIGN KEY (playlist_id) REFERENCES Playlists(id)
);

create table if not exists Settings(
    id int not null PRIMARY KEY,
    current_song varchar(255),
    current_volume float,
    standardize_volume boolean,
    current_tab varchar(255),
    window_width int,
    window_height int,
    background_path varchar(255),
    songs_path varchar(255),
    browser varchar(50),
    FOREIGN KEY (current_song) REFERENCES Songs(file)
);

INSERT OR IGNORE INTO Settings(id,current_song, current_volume, standardize_volume, current_tab, window_width, window_height, background_path, songs_path, browser)
values (1,null,null,null,null,null,null,null,null,null)